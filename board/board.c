#include "board.h"

volatile uint32_t board_milliseconds;
volatile uint32_t board_clock_hz;
volatile uint32_t board_led_state;

static uint32_t wait_ready(volatile uint32_t *reg, uint32_t mask)
{
    uint32_t timeout = 2000000U;
    while ((*reg & mask) != mask) {
        if (--timeout == 0U) { return 0U; }
    }
    return 1U;
}

static void clock_config(void)
{
    /* STM32F407: 8 MHz HSE, PLL M=8 N=336 P=2 Q=7 => 168 MHz.
       This MCU does not use the GD32 high-drive ready bits. */
    RCC->APB1ENR |= RCC_APB1ENR_PWREN;
    (void)RCC->APB1ENR;
    PWR->CR |= PWR_CR_VOS;
    RCC->CR |= RCC_CR_HSEON;
    if (wait_ready(&RCC->CR, RCC_CR_HSERDY) == 0U) { return; }
    /* At 3.3 V, 168 MHz requires 5 Flash wait states before switching. */
    FLASH->ACR = FLASH_ACR_LATENCY_5WS | FLASH_ACR_PRFTEN |
                 FLASH_ACR_ICEN | FLASH_ACR_DCEN;
    (void)FLASH->ACR;
    RCC->CFGR = (RCC->CFGR & ~(RCC_CFGR_HPRE | RCC_CFGR_PPRE1 | RCC_CFGR_PPRE2)) |
                RCC_CFGR_PPRE1_DIV4 | RCC_CFGR_PPRE2_DIV2;
    RCC->PLLCFGR = 8U | (336U << 6U) | RCC_PLLCFGR_PLLSRC_HSE | (7U << 24U);
    RCC->CR |= RCC_CR_PLLON;
    if (wait_ready(&RCC->CR, RCC_CR_PLLRDY) == 0U) { return; }
    RCC->CFGR = (RCC->CFGR & ~RCC_CFGR_SW) | RCC_CFGR_SW_PLL;
    /* Bound the switch wait too; SystemCoreClockUpdate reads actual status. */
    (void)wait_ready(&RCC->CFGR, RCC_CFGR_SWS_PLL);
}

void SysTick_Handler(void)
{
    ++board_milliseconds;
}

void board_init(void)
{
    clock_config();
    SystemCoreClockUpdate();
    board_clock_hz = SystemCoreClock;
    SCB->VTOR = FLASH_BASE;
    __DSB();
    __ISB();
    RCC->AHB1ENR |= RCC_AHB1ENR_GPIOBEN;
    (void)RCC->AHB1ENR;
    GPIOB->BSRR = 1U << 18U;
    GPIOB->MODER = (GPIOB->MODER & ~(3U << 4U)) | (1U << 4U);
    GPIOB->OTYPER &= ~(1U << 2U);
    GPIOB->OSPEEDR &= ~(3U << 4U);
    GPIOB->PUPDR &= ~(3U << 4U);
    if (SysTick_Config(SystemCoreClock / 1000U) != 0U) {
        while (1) { }
    }
    NVIC_SetPriority(SysTick_IRQn, 0U);
    __enable_irq();
}

void delay_ms(uint32_t duration)
{
    const uint32_t start = board_milliseconds;
    while ((uint32_t)(board_milliseconds - start) < duration) { __WFI(); }
}

void led_set(uint32_t on)
{
    board_led_state = (on != 0U);
    GPIOB->BSRR = (on != 0U) ? (1U << 2U) : (1U << 18U);
}
