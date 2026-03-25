import json
import os

def clean_data_safe(input_file, output_file):
    if not os.path.exists(input_file):
        print(f"❌ Error: Could not find {input_file}")
        return

    print(f"🛡️  SAFE MODE: Cleaning {input_file} (Merging Duplicates Only)...")
    with open(input_file, 'r') as f:
        data = json.load(f)
    
    initial_count = len(data)
    cleaned_data = []
    
    # Track unique content per page to find duplicates
    # Key = (page_number, text_content)
    seen_content = {} 
    
    # Priority for labels (Higher number = Better structure)
    # We want to keep 'Title' over 'UncategorizedText'
    label_priority = {
        "Title": 10,
        "Header": 8,
        "Footer": 5,
        "NarrativeText": 5,
        "Table": 4,          
        "UncategorizedText": 1,
        "Image": 0
    }

    stats = {
        "duplicates_merged": 0,
        "labels_upgraded": 0
    }
    
    for element in data:
        text = element.get("text", "").strip()
        page = element.get("metadata", {}).get("page_number", 0)
        category = element.get("type", "UncategorizedText")
        
        # skip empty text entirely (truly useless)
        if not text:
            continue

        # Create a unique key for this specific text on this specific page
        key = (page, text)
        
        if key in seen_content:
            # DUPLICATE FOUND!
            # We already have this text on this page. 
            # Let's see if the new one has a better label.
            
            existing_index = seen_content[key]
            existing_el = cleaned_data[existing_index]
            
            existing_score = label_priority.get(existing_el["type"], 1)
            current_score = label_priority.get(category, 1)
            
            if current_score > existing_score:
                # The new element is better (e.g. it's a Title, old was Uncategorized)
                # Overwrite the old one with this new one
                cleaned_data[existing_index] = element
                stats["labels_upgraded"] += 1
            
            # Whether we upgraded or not, we count this as a merge
            stats["duplicates_merged"] += 1
            continue

        # If it's new, add it to our clean list
        cleaned_data.append(element)
        seen_content[key] = len(cleaned_data) - 1

    # --- REPORT ---
    print(f"\n✨ SAFE CLEANUP REPORT")
    print(f"   -----------------------------------")
    print(f"   Original Elements:    {initial_count}")
    print(f"   👯 Duplicates Merged: {stats['duplicates_merged']}")
    print(f"   ⬆️  Labels Upgraded:   {stats['labels_upgraded']} (e.g. Uncategorized -> Title)")
    print(f"   -----------------------------------")
    print(f"   ✅ Final Elements:    {len(cleaned_data)}")
    
    with open(output_file, 'w') as f:
        json.dump(cleaned_data, f, indent=2)
    print(f"\n💾 Saved safe data to: {output_file}")

if __name__ == "__main__":
    clean_data_safe("apollo_11_turbo_results.json", "apollo_11_safe.json")