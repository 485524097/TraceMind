package demo;

public class UserService {
    private final String sourceName = "demo";

    public UserService() {
    }

    public UserService(String sourceName) {
        this.sourceName = sourceName;
    }

    public String source(String username) {
        String normalized = username == null ? "anonymous" : username.trim();
        return sourceName + ":" + normalized;
    }

    public String source(int id) {
        return sourceName + ":" + id;
    }
}
