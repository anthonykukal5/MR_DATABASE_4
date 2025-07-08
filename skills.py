from flask import Blueprint
import pandas as pd
from flask import current_app
from extensions import db
from models import Skill

skills_bp = Blueprint('skills', __name__)


def load_skills_from_excel():
    try:
        print("Attempting to load skills from Excel...")
        df = pd.read_excel('skills.xlsx')
        print("Excel columns found:", df.columns.tolist())
        print("\nFirst few rows of data:")
        print(df.head())
        required_columns = ['Lore Category', 'Sub Category', 'Skill Name', 'Status']
        missing_columns = [col for col in required_columns if col not in df.columns]
        if missing_columns:
            raise ValueError(f"Missing required columns in Excel file: {', '.join(missing_columns)}")
        for index, row in df.iterrows():
            try:
                lore_category = str(row['Lore Category']).strip()
                sub_category = str(row['Sub Category']).strip()
                skill_name = str(row['Skill Name']).strip()
                try:
                    cost = int(row['Status'])
                except (ValueError, TypeError):
                    status_str = str(row['Status']).strip()
                    status_str = ''.join(c for c in status_str if c.isdigit() or c == '.')
                    if status_str:
                        cost = int(float(status_str))
                    else:
                        print(f"Warning: Invalid status value for skill '{skill_name}': {row['Status']}")
                        continue
                rank = None
                if 'Rank' in df.columns and pd.notna(row.get('Rank')):
                    try:
                        rank = int(row['Rank'])
                    except (ValueError, TypeError):
                        print(f"Warning: Invalid rank value for skill '{skill_name}': {row.get('Rank')}")
                resources = 0
                if 'Resources' in df.columns and pd.notna(row.get('Resources')):
                    try:
                        resources = int(row['Resources'])
                    except (ValueError, TypeError):
                        resources_str = str(row['Resources']).strip()
                        resources_str = ''.join(c for c in resources_str if c.isdigit() or c == '.')
                        if resources_str:
                            resources = int(float(resources_str))
                existing_skill = Skill.query.filter_by(
                    lore_category=lore_category,
                    sub_category=sub_category,
                    name=skill_name
                ).first()
                if not existing_skill:
                    skill = Skill(
                        lore_category=lore_category,
                        sub_category=sub_category,
                        name=skill_name,
                        cost=cost,
                        rank=rank,
                        resources=resources
                    )
                    db.session.add(skill)
                    print(f"Added skill: {skill_name} ({lore_category} - {sub_category}) with cost {cost} and resources {resources}")
                else:
                    existing_skill.cost = cost
                    existing_skill.rank = rank
                    existing_skill.resources = resources
                    print(f"Updated skill: {skill_name} ({lore_category} - {sub_category}) to cost {cost}, rank {rank}, resources {resources}")
            except Exception as row_error:
                print(f"Error processing row {index + 2}: {str(row_error)}")
                continue
        db.session.commit()
        print("\nSuccessfully loaded all skills from Excel file!")
    except Exception as e:
        print(f"Error loading skills: {str(e)}")
        db.session.rollback()
        raise 