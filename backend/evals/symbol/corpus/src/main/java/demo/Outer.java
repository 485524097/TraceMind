package demo;

public class Outer {
    public static class Nested {
        public String run(String input) {
            return "nested:" + input;
        }
    }
}
