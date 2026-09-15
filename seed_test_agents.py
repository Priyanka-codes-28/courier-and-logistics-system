from app import create_app
from models import User, DeliveryAgent, Warehouse, get_db
#create flask application
app = create_app()
#use the flask application context to access the database
with app.app_context():
    warehouse = Warehouse.get_first()
    warehouse_id = warehouse["id"] if warehouse else None

    if not warehouse_id:
        print("No warehouse exists yet — create one first (see migration/setup steps), then re-run this script.")
    else:
        created_users = 0
        created_rows = 0
        for i in range(1, 11):
            email = f"agent{i}@courieros.com"
            existing_user = User.find_by_email(email)

            if existing_user:
                user_id = existing_user["id"]
                print(f"User already exists: {email}")
            else:
                user_id = User.create(
                    name=f"Agent {i}",
                    email=email,
                    phone=f"90000000{i:02d}",
                    password="Agent@1234",
                    role="delivery_agent",
                )
#display the newly created user
                print(f"Created user: {email}  (Agent {i})")
                created_users += 1

            existing_agent_row = DeliveryAgent.find_by_user_id(user_id)
            if existing_agent_row:
                print(f"  delivery_agents row already exists for {email}")
            else:
                db = get_db()
                with db.cursor() as cur:
                    cur.execute(
                        """INSERT INTO delivery_agents (user_id, warehouse_id, is_available)
                           VALUES (%s, %s, TRUE)""",
                        (user_id, warehouse_id),
                    )
                print(f"  Created missing delivery_agents row for {email}")
                created_rows += 1

        print()
#display the number of users and delivery agent records created
        print(f"Done. {created_users} new user(s), {created_rows} delivery_agents row(s) created/repaired.")
#display the login credentials for the test delivery agents
        print("Log in with agent1@courieros.com ... agent10@courieros.com, password: Agent@1234")