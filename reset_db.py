from app import app, db

with app.app_context():
    # Yeh purane tamam tables delete kar dega
    db.drop_all()
    print("Old tables dropped.")
    
    # Yeh naye USA EHR standard columns ke sath fresh tables banayega
    db.create_all()
    print("New tables with all columns (including middle_name) created successfully!")