// Sample: pointers, struct, enum, typedef, switch, macros, 2-D arrays
#include <stdio.h>
#include <stdlib.h>

#define SIZE 3
#define SQUARE(x) ((x) * (x))

typedef struct Node {
    int value;
    struct Node *next;
} Node;

enum Mode { ADD, MULTIPLY = 5 };

Node *push(Node *head, int v) {
    Node *n = (Node *)malloc(sizeof(Node));
    n->value = v;
    n->next = head;
    return n;
}

void swap(int *a, int *b) {
    int t = *a;
    *a = *b;
    *b = t;
}

int main() {
    int grid[SIZE][SIZE];
    int x = 1, y = 2, n;
    Node *list = NULL;
    enum Mode mode = MULTIPLY;

    scanf("%d", &n);
    for (int i = 0; i < SIZE; i++)
        for (int j = 0; j < SIZE; j++)
            grid[i][j] = SQUARE(i + j);

    switch (mode) {
        case ADD:      x = x + n; break;
        case MULTIPLY: x = x * n; break;
        default:       break;
    }
    swap(&x, &y);
    list = push(list, x > y ? x : y);
    printf("%d %d %d\n", x, y, list->value);
    return 0;
}
