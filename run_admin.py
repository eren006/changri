import os

from admin import create_app

app = create_app()

if __name__ == "__main__":
    app.run(host=os.getenv("ADMIN_HOST", "127.0.0.1"), port=int(os.getenv("ADMIN_PORT", "5091")))
