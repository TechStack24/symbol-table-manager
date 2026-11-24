// Sample 2: prototypes, recursion and function calls
int factorial(int n);               // prototype: declared, not yet defined
int fib(int n);
float average(int a, int b);

int factorial(int n) {
    if (n <= 1) {
        return 1;
    }
    return n * factorial(n - 1);    // the function name is found in the global scope
}

int fib(int n) {
    if (n < 2) return n;
    return fib(n - 1) + fib(n - 2);
}

float average(int a, int b) {
    return (a + b) / 2.0;
}

int main() {
    int i;
    int total = 0;
    for (i = 1; i <= 5; i++) {
        total += factorial(i);
    }
    printf("sum of factorials = %d\n", total);
    printf("fib(10) = %d\n", fib(10));
    printf("average = %f\n", average(3, 4));
    return 0;
}
