#include "lamp.h"
#include "main.h"
#include "breath_curve.h"
#include <stdio.h>
#include <string.h>
volatile uint32_t board_milliseconds,board_clock_hz,board_led_state;
/* PB2 has no timer channel: TIM3 IRQ mirrors the PA6/TIM3_CH1 waveform.
 * PWM carrier 1 kHz; CCR preload changes duty only at a period boundary. */
volatile uint32_t pwm_request,pwm_active,pwm_cycles,pwm_compares;
volatile uint32_t effect_mode,brightness_limit=1000U,effect_period_ms=3000U;
volatile uint32_t key_actions,key_long_actions,app_ready,uart_errors;
volatile uint32_t effect_paused,uart_rx_errors;
volatile uint32_t serial_commands,serial_bad_commands,serial_rx_dropped;
volatile uint32_t brightness_effective=1000U,uart_tx_dropped,wave_packets;
static volatile uint8_t tx_ring[512];
static volatile uint32_t tx_head,tx_tail;
static uint32_t fade_from=1000U,fade_target=1000U,fade_elapsed=300U;
static volatile uint8_t rx_ring[128];
static volatile uint32_t rx_head,rx_tail,rx_desync;
static uint32_t status_pending;
static uint32_t next_duty,dim_accumulator,phase_ms;
static uint32_t rng=0x98765432U,fire_level,fire_target,fire_wait;
static const char *names[4]={"BREATHE","HEARTBEAT","FIREFLY","STEADY"};

/* HAL owns peripheral setup and interrupt dispatch. Application callbacks keep
 * the ring buffers and PB2 mirror independent of serial message formatting. */
static TIM_HandleTypeDef *lamp_timer;
static UART_HandleTypeDef *lamp_uart;
static uint32_t last_effect,last_status,last_wave;
static uint8_t uart_rx_byte;
static volatile uint32_t tx_busy,tx_chunk;

void HAL_TIM_PWM_PulseFinishedCallback(TIM_HandleTypeDef *htim)
{
 if (htim->Instance==TIM3 && htim->Channel==HAL_TIM_ACTIVE_CHANNEL_1) {
  HAL_GPIO_WritePin(GPIOB,GPIO_PIN_2,GPIO_PIN_RESET);++pwm_compares;
 }
}

void HAL_TIM_PeriodElapsedCallback(TIM_HandleTypeDef *htim)
{
 if (htim->Instance!=TIM3) { return; }
 uint32_t duty=next_duty;
 pwm_active=duty;++pwm_cycles;
 /* A compare flag at the period boundary belongs to the previous cycle. */
 __HAL_TIM_CLEAR_FLAG(htim,TIM_FLAG_CC1);
 HAL_GPIO_WritePin(GPIOB,GPIO_PIN_2,
  (duty>=1000U || (duty && __HAL_TIM_GET_COUNTER(htim)<duty)) ? GPIO_PIN_SET:GPIO_PIN_RESET);
 if (duty && duty<1000U) { __HAL_TIM_ENABLE_IT(htim,TIM_IT_CC1); }
 else { __HAL_TIM_DISABLE_IT(htim,TIM_IT_CC1); }
 uint32_t request=pwm_request;
 if (request>1000U) { request=1000U; }
 /* Preserve the original low-level pulse-density behaviour. */
 if (request && request<4U) {
  dim_accumulator+=request;
  request=dim_accumulator>=4U ? 4U:0U;
  if (request) { dim_accumulator-=4U; }
 } else { dim_accumulator=0U; }
 __HAL_TIM_SET_COMPARE(htim,TIM_CHANNEL_1,request);next_duty=request;
}

void HAL_UART_RxCpltCallback(UART_HandleTypeDef *huart)
{
 if (huart->Instance!=USART1) { return; }
 uint32_t next=(rx_head+1U)&127U;
 if (HAL_UART_GetError(huart)!=HAL_UART_ERROR_NONE) { rx_desync=1U;return; }
 else if (next==rx_tail) { ++serial_rx_dropped;rx_desync=1U; }
 else { rx_ring[rx_head]=uart_rx_byte;__DMB();rx_head=next; }
 (void)HAL_UART_Receive_IT(huart,&uart_rx_byte,1U);
}

void HAL_UART_ErrorCallback(UART_HandleTypeDef *huart)
{
 if (huart->Instance!=USART1) { return; }
 ++uart_rx_errors;rx_desync=1U;
 /* ORE terminates HAL reception. FE/NE may leave it running. */
 if (huart->RxState==HAL_UART_STATE_READY) {
  (void)HAL_UART_Receive_IT(huart,&uart_rx_byte,1U);
 }
}

/* Called with interrupts masked, or from the USART callback. The ring tail
 * advances only after HAL has finished reading the associated memory. */
static void uart_tx_start(void)
{
 if (tx_busy || tx_tail==tx_head) { return; }
 uint32_t count=tx_head>tx_tail ? tx_head-tx_tail:512U-tx_tail;
 tx_chunk=count;tx_busy=1U;
 if (HAL_UART_Transmit_IT(lamp_uart,(const uint8_t *)&tx_ring[tx_tail],(uint16_t)count)!=HAL_OK) {
  tx_busy=0U;++uart_errors;
 }
}

void HAL_UART_TxCpltCallback(UART_HandleTypeDef *huart)
{
 if (huart->Instance!=USART1) { return; }
 tx_tail=(tx_tail+tx_chunk)&511U;tx_busy=0U;uart_tx_start();
}

static void uart_text(const char *line)
{
 uint32_t n=(uint32_t)strlen(line),head=tx_head;
 if (n>((tx_tail-head-1U)&511U)) { ++uart_tx_dropped;return; }
 for (uint32_t i=0U;i<n;++i) { tx_ring[(head+i)&511U]=(uint8_t)line[i]; }
 uint32_t mask=__get_PRIMASK();__disable_irq();
 __DMB();tx_head=(head+n)&511U;uart_tx_start();__set_PRIMASK(mask);
}
static void print_status(void)
{
 char line[220];
 (void)snprintf(line,sizeof(line),"STATE mode=%lu name=%s brightness=%lu period=%lu pause=%lu duty=%lu uptime=%lu rxerr=%lu dropped=%lu cmds=%lu effective=%lu txdrop=%lu\r\n",
  (unsigned long)effect_mode,names[effect_mode],(unsigned long)brightness_limit,
  (unsigned long)effect_period_ms,(unsigned long)effect_paused,(unsigned long)pwm_request,
  (unsigned long)board_milliseconds,(unsigned long)uart_rx_errors,
  (unsigned long)serial_rx_dropped,(unsigned long)serial_commands,
  (unsigned long)brightness_effective,(unsigned long)uart_tx_dropped);
 uart_text(line);
}
static void serial_command(const char *line)
{
 if (!strcmp(line,"GET")) { ++serial_commands;status_pending=1U;return; }
 if (!strcmp(line,"DEFAULT")) {
  effect_mode=0U;brightness_limit=1000U;effect_period_ms=3000U;effect_paused=0U;
  phase_ms=0U;fire_level=0U;fire_wait=0U;
  ++serial_commands;status_pending=1U;uart_text("OK DEFAULT\r\n");return;
 }
 char command=line[0];
 if (!(command=='M'||command=='B'||command=='T'||command=='P') || line[1]!=' ' || !line[2]) {
  ++serial_bad_commands;uart_text("ERR COMMAND\r\n");return;
 }
 uint32_t value=0U;
 for (uint32_t i=2U;line[i];++i) {
  if (line[i]<'0'||line[i]>'9') { ++serial_bad_commands;uart_text("ERR COMMAND\r\n");return; }
  value=value*10U+(uint32_t)(line[i]-'0');
  if (value>10000U) { ++serial_bad_commands;uart_text("ERR RANGE\r\n");return; }
 }
 if ((command=='M'&&value>3U)||(command=='B'&&value>1000U)||
     (command=='P'&&value>1U)||(command=='T'&&(value<500U||value>10000U))) {
  ++serial_bad_commands;uart_text("ERR RANGE\r\n");return;
 }
 if (command=='M') { effect_mode=value;phase_ms=0U;fire_wait=0U; }
 else if (command=='B') { brightness_limit=value; }
 else if (command=='T') { phase_ms=phase_ms*value/effect_period_ms;effect_period_ms=value; }
 else { effect_paused=value; }
 char reply[32];(void)snprintf(reply,sizeof(reply),"OK %c %lu\r\n",command,(unsigned long)value);
 ++serial_commands;status_pending=1U;uart_text(reply);
}
static void serial_service(void)
{
 static char line[40];static uint32_t length,discard;
 for (uint32_t budget=0U;budget<32U;++budget) {
  if (rx_desync) {
   uint32_t mask=__get_PRIMASK();__disable_irq();
   rx_tail=rx_head;rx_desync=0U;__set_PRIMASK(mask);
   discard=1U;length=0U;uart_text("ERR RX\r\n");
  }
  if (rx_tail==rx_head) { break; }
  char c=(char)rx_ring[rx_tail];rx_tail=(rx_tail+1U)&127U;
  if (c=='\r') { continue; }
  if (c=='\n') {
   if (rx_desync) { continue; }
   if (discard) { discard=0U;length=0U;uart_text("ERR COMMAND\r\n"); }
   else if (length) { line[length]='\0';serial_command(line);length=0U; }
  } else if (!discard) {
   if (length+1U<sizeof(line)) { line[length++]=c; }
   else { discard=1U;length=0U;++serial_bad_commands; }
  }
 }
}
static void next_mode(void) { effect_mode=(effect_mode+1U)%4U;phase_ms=0U; }
static void dim(void)
{
 brightness_limit=brightness_limit==1000U ? 350U:(brightness_limit==350U ? 100U:1000U);
}
static void input_service(uint32_t now)
{
 static uint32_t last_raw,stable,changed,pressed,long_done;
 uint32_t raw=HAL_GPIO_ReadPin(GPIOA,GPIO_PIN_0)==GPIO_PIN_SET;
 if (raw!=last_raw) { last_raw=raw;changed=now; }
 if (raw!=stable && (uint32_t)(now-changed)>=25U) {
  stable=raw;
  if (stable) { pressed=now;long_done=0U; }
  else if (!long_done) { next_mode();++key_actions; }
 }
 if (stable && !long_done && (uint32_t)(now-pressed)>=800U) {
  dim();long_done=1U;++key_long_actions;
 }

}
static uint32_t pulse(uint32_t phase,uint32_t start,uint32_t width)
{
 if (phase<start || phase>=start+width) { return 0U; }
 return breath_curve[(phase-start)*256U/width];
}
static void fade_update(uint32_t elapsed)
{
 if (brightness_limit!=fade_target) {
  fade_from=brightness_effective;fade_target=brightness_limit;fade_elapsed=0U;
 }
 fade_elapsed=elapsed>=300U-fade_elapsed ? 300U:fade_elapsed+elapsed;
 uint32_t t=fade_elapsed*1000U/300U;
 uint32_t ease=t*t*(3000U-2U*t)/1000000U;
 int32_t delta=(int32_t)fade_target-(int32_t)fade_from;
 brightness_effective=(uint32_t)((int32_t)fade_from+delta*(int32_t)ease/1000);
}
static void print_wave(void)
{
 char line[160];
 (void)snprintf(line,sizeof(line),"WAVE t=%lu duty=%lu ccr=%lu eff=%lu target=%lu period=%lu mode=%lu hz=1000 pause=%lu\r\n",
  (unsigned long)board_milliseconds,(unsigned long)pwm_request,(unsigned long)pwm_active,
  (unsigned long)brightness_effective,(unsigned long)brightness_limit,
  (unsigned long)effect_period_ms,(unsigned long)effect_mode,(unsigned long)effect_paused);
 uart_text(line);++wave_packets;
}
static void effect_update(uint32_t elapsed)
{
 fade_update(elapsed);
 if (!effect_paused) { phase_ms=(phase_ms+elapsed)%effect_period_ms; }
 uint32_t level;
 if (effect_mode==0U) { level=breath_curve[phase_ms*256U/effect_period_ms]; }
 else if (effect_mode==1U) {
  uint32_t p=phase_ms*2000U/effect_period_ms;
  level=pulse(p,0U,250U)+pulse(p,330U,200U)*650U/1000U;
 } else if (effect_mode==2U) {
  if (!effect_paused && elapsed>=fire_wait) {
   rng^=rng<<13U;rng^=rng>>17U;rng^=rng<<5U;
   fire_target=(rng&3U) ? (rng%120U):(350U+rng%651U);
   fire_wait=100U+rng%350U;
  }
  else if (!effect_paused) { fire_wait-=elapsed; }
  if (!effect_paused && fire_level<fire_target) { fire_level+=(fire_target-fire_level+7U)/8U; }
  else if (!effect_paused) { fire_level-=(fire_level-fire_target+15U)/16U; }
  level=fire_level*fire_level/1000U;
 } else { level=1000U; }
 pwm_request=level*brightness_effective*brightness_effective/1000000U;
}
/* CubeMX initialises clocks, GPIO, TIM3 and USART1 before this call.
 * No generated file is owned by the application module. */
void Lamp_Init(TIM_HandleTypeDef *timer,UART_HandleTypeDef *uart)
{
 if (timer==NULL || uart==NULL || timer->Instance!=TIM3 || uart->Instance!=USART1) {
  Error_Handler();return;
 }
 lamp_timer=timer;lamp_uart=uart;
 board_clock_hz=HAL_RCC_GetHCLKFreq();board_milliseconds=HAL_GetTick();
 last_effect=board_milliseconds;last_status=last_effect;last_wave=last_effect;
 __HAL_DBGMCU_FREEZE_TIM3();
 __HAL_TIM_SET_COMPARE(lamp_timer,TIM_CHANNEL_1,0U);
 __HAL_TIM_CLEAR_FLAG(lamp_timer,TIM_FLAG_UPDATE|TIM_FLAG_CC1);
 if (HAL_TIM_PWM_Start(lamp_timer,TIM_CHANNEL_1)!=HAL_OK) { Error_Handler(); }
 if (HAL_TIM_Base_Start_IT(lamp_timer)!=HAL_OK) { Error_Handler(); }
 if (HAL_UART_Receive_IT(lamp_uart,&uart_rx_byte,1U)!=HAL_OK) { Error_Handler(); }
 app_ready=1U;
}

void Lamp_Task(void)
{
 uint32_t now=HAL_GetTick();board_milliseconds=now;
 input_service(now);serial_service();
 if ((uint32_t)(now-last_effect)>=5U) {
  uint32_t dt=now-last_effect;last_effect=now;effect_update(dt);
 }
 if (status_pending || (uint32_t)(now-last_status)>=500U) {
  status_pending=0U;last_status=now;print_status();
 }
 if ((uint32_t)(now-last_wave)>=40U) { last_wave=now;print_wave(); }
}
