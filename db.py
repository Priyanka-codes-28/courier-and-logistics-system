import psycopg2
import psycopg2.extras
from flask import current_app, g


def get_db():
    if "db" not in g:
        g.db = psycopg2.connect(
            host=current_app.config["PG_HOST"],
            port=current_app.config["PG_PORT"],
            user=current_app.config["PG_USER"],
            password=current_app.config["PG_PASSWORD"],
            dbname=current_app.config["PG_DB"],
                sslmode="require",
            cursor_factory=psycopg2.extras.RealDictCursor,
        )
        g.db.autocommit = True
    return g.db


def close_db(e=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_app(app):
    app.teardown_appcontext(close_db)