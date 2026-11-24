// Sample 3: semantic errors that the symbol table helps us detect
int counter = 0;
int counter;                        // redeclaration in the same scope

int square(int n) {
    return n * n;
}

void report(int code) {
    int unused_local = 7;           // declared but never used
    printf("code %d\n", code);
}

int main() {
    int a = 10;
    float ratio = 3.5;
    int total;
    a = ratio;                      // float -> int loses precision
    total = total + 1;              // total is read before it is initialized
    b = a + 1;                      // 'b' was never declared
    a = square(1, 2);               // wrong number of arguments
    ghost(5);                       // function was never declared
    int a;                          // 'a' already exists in this scope
    break;                          // not inside a loop
    return report(3);               // report() returns void
}
