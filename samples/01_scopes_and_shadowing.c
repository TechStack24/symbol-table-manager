// Sample 1: nested scopes and shadowing
int level = 0;                      // global scope

int main() {
    int x = 10;
    float y = 2.5;

    {
        int x = 20;                 // shadows the outer x
        char grade = 'A';
        printf("inner x = %d, grade = %c\n", x, grade);
    }

    for (int i = 0; i < 3; i++) {   // 'i' lives in the for-loop scope
        int square = i * i;
        x = x + square;
    }

    if (x > 15) {
        float y = 0.5;              // another shadow, this time of y
        level = level + 1;
        printf("inner y = %f\n", y);
    }

    printf("x = %d, y = %f, level = %d\n", x, y, level);
    return 0;
}
