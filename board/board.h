#ifndef BOARD_H
#define BOARD_H
#include "stm32f4xx.h"
void board_init(void);
void delay_ms(uint32_t duration);
void led_set(uint32_t on);
#endif
