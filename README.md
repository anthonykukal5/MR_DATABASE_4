# Mystic Realms Character Database

A web application for managing users characters in the Mystic Realms universe.

## Features

- User registration and authentication
- Character creation and management
- Status point allocation system
- Skill management with categories and subcategories
- Realm-specific species selection
- Health and stamina management
- Arbitration Management
- Print Screen
- Automatic email updates (not implemented)


## Setup

1. Create a virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

2. Install dependencies:
```bash
pip install -r requirements.txt  # Out of date
```

4. Run the application:
```bash
python app.py
```

5. Access the application at `http://localhost:5000`

## Database Structure

The application uses Postsgres as its database. The database will be automatically created when you first run the application.
