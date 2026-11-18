package com.mes.dto;

public class LoginResponse {

    private String token;
    private Object user;

    public LoginResponse(String token, Object user) {
        this.token = token;
        this.user = user;
    }

    public String getToken() { return token; }
    public void setToken(String token) { this.token = token; }
    public Object getUser() { return user; }
    public void setUser(Object user) { this.user = user; }
}
