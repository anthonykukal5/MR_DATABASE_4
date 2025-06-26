# Mystic Realms Character Database

A web application for managing characters in the Mystic Realms universe.

## Features

- User registration and authentication
- Character creation and management
- Status point allocation system
- Skill management with categories and subcategories
- Realm-specific species selection
- Health and stamina management

## Setup

1. Create a virtual environment:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Create a `skills.xlsx` file in the root directory with the following columns:
   - Lore Category
   - Sub Category
   - Skill Name
   - Cost
   - Rank (optional)

4. Run the application:
```bash
python app.py
```

5. Access the application at `http://localhost:5000`

## Database Structure

The application uses SQLite as its database. The database will be automatically created when you first run the application.

### Models

- User: Stores user account information
- Character: Stores character information
- Skill: Stores skill information
- CharacterSkill: Links characters to their skills

## Contributing

1. Fork the repository
2. Create a feature branch
3. Commit your changes
4. Push to the branch
5. Create a Pull Request 