from flask import Flask, render_template
from config import Config
import db
from routes.auth import auth_bp


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    db.init_app(app)
    app.register_blueprint(auth_bp)

    @app.route("/")
    def home():
        return render_template("landing.html")

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(debug=True)