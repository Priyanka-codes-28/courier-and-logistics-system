from authlib.integrations.flask_client import OAuth

oauth = OAuth()
GOOGLE_OAUTH_CONFIGURED = False
def init_app(app):
    global GOOGLE_OAUTH_CONFIGURED
    oauth.init_app(app)

    client_id = app.config.get("GOOGLE_CLIENT_ID")
    client_secret = app.config.get("GOOGLE_CLIENT_SECRET")
    if not client_id or not client_secret:
        app.logger.warning(
            "[google_oauth] GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET not set — "
            "'Continue with Google' will show a friendly error until both are configured."
        )
        return

    oauth.register(
        name="google",
        client_id=client_id,
        client_secret=client_secret,
        server_metadata_url=app.config["GOOGLE_DISCOVERY_URL"],
        client_kwargs={"scope": "openid email profile"},
    )
    GOOGLE_OAUTH_CONFIGURED = True
