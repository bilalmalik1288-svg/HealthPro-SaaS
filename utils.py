import os
import uuid
import pandas as pd
from werkzeug.utils import secure_filename

# USA EHR Standards ke mutabiq allowed files
ALLOWED_EXTENSIONS = {'csv', 'xls', 'xlsx'}

def allowed_file(filename):
    """Check karta hai ke upload ki gayi file CSV ya Excel hai ya nahi."""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def process_patient_import(filepath):
    """
    CSV ya Excel file ko read kar ke patient data extract karta hai.
    Missing MRN khud generate karta hai aur data ko clean karta hai.
    """
    try:
        # File type check kar ke read karein
        if filepath.endswith('.csv'):
            df = pd.read_csv(filepath)
        else:
            # Excel files ke liye openpyxl engine zaroori hai
            df = pd.read_excel(filepath, engine='openpyxl')
        
        # NaN (khali) values ko empty string se replace karein taake database mein error na aaye
        df = df.fillna('')
        
        patients_list = []
        
        # Expected columns. Agar client ki CSV mein column names thode mukhtalif hon, 
        # toh aap yahan adjust kar sakte hain.
        for index, row in df.iterrows():
            # Agar MRN pehle se nahi hai, toh automatic secure MRN generate karein
            mrn = str(row.get('MRN', '')).strip()
            if not mrn:
                mrn = f"MRN-{uuid.uuid4().hex[:6].upper()}"

            patient_data = {
                'mrn': mrn,
                'first_name': str(row.get('First Name', '')).strip(),
                'last_name': str(row.get('Last Name', '')).strip(),
                'dob': str(row.get('DOB', '')).strip(), # Format: YYYY-MM-DD ideally
                'gender': str(row.get('Gender', '')).strip(),
                'phone': str(row.get('Phone', '')).strip(),
                'email': str(row.get('Email', '')).strip(),
                'address': str(row.get('Address', '')).strip(),
                'insurance_provider': str(row.get('Insurance Provider', '')).strip(),
                'policy_number': str(row.get('Policy Number', '')).strip()
            }
            
            # Basic Validation: Kam az kam First Name aur Last Name hona zaroori hai
            if patient_data['first_name'] and patient_data['last_name']:
                patients_list.append(patient_data)
                
        return True, patients_list, "Data processed successfully."
        
    except Exception as e:
        # Agar file corrupt ho ya format galat ho
        return False, [], f"Error processing file: {str(e)}"