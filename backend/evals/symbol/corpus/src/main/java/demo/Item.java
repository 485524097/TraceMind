package demo;

public record Item(String name, int count) {
    public Item {
        if (count < 0) {
            throw new IllegalArgumentException("count");
        }
    }
}
