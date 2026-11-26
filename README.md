# 天空星 STM32F407 PWM 灯效：CubeMX＋Keil

## 从哪里打开
- 配置引脚、时钟和外设：STM32CubeMX 打开根目录 TKX_STM32F407_PWM_Breathe.ioc。
- 编写、编译和调试：Keil 打开 MDK-ARM/TKX_STM32F407_PWM_Breathe.uvprojx。
- 电脑控制界面：运行 启动控制界面.ps1，默认 COM16；可用 -Port COMxx 指定串口。
- 编译：PowerShell运行 ./build.ps1。
- 重新生成并编译：运行 ./重新生成工程.ps1 -Build；也可在CubeMX直接Generate Code，再在Keil构建。

## 工程结构
Core/Inc、Core/Src：CubeMX实际生成的main、GPIO、TIM、USART、IRQ和HAL配置。
Drivers：CubeMX复制的ST HAL与ARM CMSIS依赖，编译不依赖旧libraries目录。
MDK-ARM：实际生成的Keil工程、启动汇编、配置、构建结果和日志。
app/lamp.c、lamp.h：独立业务模块，保留四灯效、300ms亮度过渡、按键、UART缓冲与协议。
app/breath_curve.h：呼吸包络查找表。
control_panel.py：Tkinter控制与实时曲线同屏界面。
.venv：Python串口运行环境，仍需本机Python 3.12。
.git：版本记录，不参与运行。

## 重新生成的边界
.ioc管理MCU、时钟、引脚、TIM3、USART1与NVIC；不要手改生成区作为永久配置。
Core/Src/main.c仅在USER CODE中包含lamp.h、调用Lamp_Init(&htim3,&huart1)，主循环调用Lamp_Task及__WFI。
业务代码只改app；不要再添加第二份main、IRQ、SystemClock_Config或外设句柄。
Keil的Application组引用../app/lamp.c，include路径含../app。已实际重新生成并确认保留。
Keil已选择Arm Compiler 6.24和CMSIS-DAP；对ST驱动跨芯片通用的未用参数关闭unused-parameter诊断，其余编译检查保留。
生成环境：CubeMX 6.16.1，本机固件包D:/stm32Documents/STM32Cube_FW_F4_V1.28.2；换电脑重新生成时先安装对应包，并在CubeMX选择本机位置。编译需要Keil及STM32F4设备包。

## 硬件
STM32F407VETx；HSE 8MHz，SYSCLK 168MHz，APB1 42MHz、APB2 84MHz。
PA6=TIM3_CH1，PSC83、ARR999，PWM 1kHz；PB2板载绿灯通过HAL定时器回调镜像。
PA0 KEY高电平按下，下拉；25ms软件消抖，短按切灯效，长按800ms切100%/35%/10%亮度上限。
USART1：PA9→DAP RXD，PA10←DAP TXD，GND共地；115200 8N1。SWD线用于下载调试。
中断优先级：TIM3=0，USART1=1，SysTick=2。

## 串口命令（换行结束）
M 0..3：呼吸、双心跳、萤火虫、常亮。
B 0..1000：亮度上限，经过300ms过渡与平方映射后参与占空比计算。
T 500..10000：呼吸/双心跳周期ms；萤火虫随机等待独立，常亮不使用周期。
P 0/1：恢复/暂停灯效相位；PWM继续输出。
GET：立即请求STATE；DEFAULT：恢复呼吸、100%上限、3秒周期。
STATE默认每500ms或命令后回传；WAVE每40ms回传；Python约50ms绘图、45ms滑块发送节流。

## 验证与清理
已由CubeMX实际生成并多次重新生成；业务/USER CODE哈希一致，Keil 6.24完整重编译0错误0警告。
已烧录并逐字节回读，实机168MHz，四种灯效及亮度/周期/暂停/越界拒绝测试通过，串口错误及丢包计数0。
见cubemx-migration-validation.json与cubemx-hardware-validation.json。
旧board、libraries、project和app/main.c已移除，唯一Keil入口为MDK-ARM中的工程；cleanup-manifest.json记录清理项。
电脑曲线是MCU参数遥测，下方PWM为参数重建，不是引脚电压实测。PA6是真硬件PWM；PB2有回调延迟，极低亮度4us脉冲仍需示波器确认。
