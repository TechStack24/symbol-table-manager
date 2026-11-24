// Sample 4: arrays, initializer lists and loops
int scores[5] = {90, 72, 85, 64, 98};
char name[8] = "symbol";

int sum_array(int count) {
    int total = 0;
    int idx = 0;
    while (idx < count) {
        total += scores[idx];
        idx++;
    }
    return total;
}

int main() {
    int best = scores[0];
    int k;
    for (k = 1; k < 5; k++) {
        if (scores[k] > best) {
            best = scores[k];
        }
    }
    printf("sum = %d, best = %d\n", sum_array(5), best);
    printf("first letter: %c\n", name[0]);
    scores[5] = 100;                // index 5 is out of bounds (size is 5)
    return 0;
}
