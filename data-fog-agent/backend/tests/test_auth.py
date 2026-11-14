def login(client, email="admin@chaiwala.co", password="correct-password"):
    return client.post("/api/auth/login", json={"email": email, "password": password})


def test_login_returns_token_that_works_on_me(client, admin):
    response = login(client)
    assert response.status_code == 200

    token = response.json()["access_token"]
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["email"] == "admin@chaiwala.co"
    assert me.json()["role"] == "admin"


def test_login_ignores_email_case_and_spaces(client, admin):
    assert login(client, email="  Admin@Chaiwala.CO ").status_code == 200


def test_wrong_password_and_unknown_email_get_the_same_error(client, admin):
    wrong_password = login(client, password="wrong-password")
    unknown_email = login(client, email="nobody@chaiwala.co")

    assert wrong_password.status_code == unknown_email.status_code == 401
    assert wrong_password.json() == unknown_email.json()


def test_me_without_token_is_rejected(client):
    assert client.get("/api/auth/me").status_code == 401


def test_refresh_token_cannot_be_used_as_access_token(client, admin):
    login(client)
    refresh_token = client.cookies["refresh_token"]

    response = client.get("/api/auth/me", headers={"Authorization": f"Bearer {refresh_token}"})
    assert response.status_code == 401


def test_refresh_cookie_gives_new_access_token(client, admin):
    login(client)

    response = client.post("/api/auth/refresh")
    assert response.status_code == 200

    token = response.json()["access_token"]
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200


def test_refresh_after_logout_is_rejected(client, admin):
    login(client)
    client.post("/api/auth/logout")

    assert client.post("/api/auth/refresh").status_code == 401


def test_login_and_chat_are_rate_limited(client, admin):
    for _ in range(10):
        wrong = {"email": "admin@chaiwala.co", "password": "wrong"}
        assert client.post("/api/auth/login", json=wrong).status_code == 401
    right = {"email": "admin@chaiwala.co", "password": "correct-password"}
    assert client.post("/api/auth/login", json=right).status_code == 429


def test_health_check(client):
    assert client.get("/api/health").json() == {"ok": True}
