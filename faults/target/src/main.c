/* A PIN check, twice: as it's usually written, and hardened against a single fault.
 * Every 50 ms both are asked about the wrong PIN; each prints one line per attempt:
 *     <n> plain denied            <n> hardened denied
 * A fault that makes either print GRANTED has bypassed it. */
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char SECRET[4] = {'4', '7', '1', '1'};
static const char GUESS[4] = {'0', '0', '0', '0'};

/* 0 when equal; no early exit (no timing leak) */
__attribute__((noinline)) int pin_diff(const char *a, const char *b)
{
    int d = 0;
    for (int i = 0; i < 4; i++)
        d |= a[i] ^ b[i];
    return d;
}

/* As it's usually written: one compare, one branch */
__attribute__((noinline)) int check_plain(const char *pin)
{
    return pin_diff(pin, SECRET) == 0;
}

__attribute__((noinline)) void attempt_plain(int n, const char *pin)
{
    if (check_plain(pin))
        printf("%d plain GRANTED\n", n);
    else
        printf("%d plain denied\n", n);
}

/* Hardened: results are values far apart in Hamming distance, not 0/1; the compare is
 * done twice, the second time with its operands swapped; the loop is checked to have run
 * to the end; and the decision is checked again where it's used. */
#define GRANT 0x3CA5965Au
#define DENY  0xC35A69A5u

__attribute__((noinline)) uint32_t check_hardened(const char *pin)
{
    volatile uint32_t result = DENY;
    volatile int d1 = 0, d2 = 0, i;
    for (i = 0; i < 4; i++)
        d1 |= pin[i] ^ SECRET[i];
    if (i != 4)
        return DENY;
    if (d1 != 0)
        return DENY;
    for (i = 0; i < 4; i++)
        d2 |= SECRET[i] ^ pin[i];
    if (i != 4 || d2 != 0 || d1 != 0)
        return DENY;
    result = GRANT;
    return result;
}

__attribute__((noinline)) void attempt_hardened(int n, const char *pin)
{
    volatile uint32_t r = check_hardened(pin);
    if (r == GRANT) {
        if (r != GRANT || check_hardened(pin) != GRANT) {
            printf("%d hardened FAULT\n", n);
            return;
        }
        printf("%d hardened GRANTED\n", n);
    } else {
        printf("%d hardened denied\n", n);
    }
}

void app_main(void)
{
    printf("fault target ready\n");
    for (int n = 0;; n++) {
        attempt_plain(n, GUESS);
        attempt_hardened(n, GUESS);
        vTaskDelay(pdMS_TO_TICKS(50));
    }
}
