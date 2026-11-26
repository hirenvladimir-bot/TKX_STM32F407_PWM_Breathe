#ifndef LAMP_H
#define LAMP_H
#include "stm32f4xx_hal.h"

/* Call after MX_GPIO_Init, MX_TIM3_Init and MX_USART1_UART_Init. */
void Lamp_Init(TIM_HandleTypeDef *timer,UART_HandleTypeDef *uart);
/* Call once per generated main-loop iteration. No blocking delays. */
void Lamp_Task(void);

#endif
