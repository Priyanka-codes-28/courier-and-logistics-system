from flask import Flask, render_template
from config import Config
import db
import google_oauth

from routes.auth import auth_bp
from routes.shipments import shipments_bp
from timezone_utils import format_ist


def create_app():
    app = Flask(__name__)

    # Load application configuration
    app.config.from_object(Config)

    # Initialize database
    db.init_app(app)

    # Initialize Google OAuth
    google_oauth.init_app(app)

    # Register blueprints
    app.register_blueprint(auth_bp)
    app.register_blueprint(shipments_bp)
    app.jinja_env.filters["ist"] = format_ist

    @app.route("/")
    def home():
        return render_template("landing.html")

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(debug=True)