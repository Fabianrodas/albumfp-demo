from application import app, create_app
from app.utils.env import int_env, truthy as _truthy

__all__ = ["app", "create_app"]


if __name__ == "__main__":
    app.run(
        host="127.0.0.1",
        port=int_env("DEV_PORT", 5000),
        debug=_truthy("FLASK_DEBUG"),
    )
