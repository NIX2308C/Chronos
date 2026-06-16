import os
from dotenv import load_dotenv
from google import genai
from pinecone import Pinecone

load_dotenv()

GEMINI_KEY = os.getenv("GEMINI_API_KEY")
PINE_KEY = os.getenv("PINECONE_API_KEY")

client = genai.Client(api_key=GEMINI_KEY)
pc = Pinecone(api_key=PINE_KEY)
pinecone_index = pc.Index("teacherchronostwo")

def feed_teacher_data(text_content, unique_id):
    print(f"\n--- Processing: {unique_id} ---")
    try:
        print("Step A: Requesting embedding from Google...")
        result = client.models.embed_content(
            model="models/gemini-embedding-001",
            contents=text_content,
            config={"output_dimensionality": 768}
        )
        embedding = result.embeddings[0].values

        print("Step B: Pushing to Pinecone...")
        pinecone_index.upsert(
            vectors=[{
                "id": unique_id,
                "values": embedding,
                "metadata": {"text": text_content}
            }]
        )
        print(f"SUCCESS: '{unique_id}' is in the database.")

    except Exception as e:
        print(f"SYSTEM ERROR: {e}")

if __name__ == "__main__":
    feed_teacher_data("Rule: Gear ratio is calculated as Driven divided by Driver.", "rule_gear_ratio")
    feed_teacher_data("Rule: Always document why a mechanism failed before fixing it.", "rule_failure_log")