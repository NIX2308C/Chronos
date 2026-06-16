import os
from dotenv import load_dotenv
from pinecone import Pinecone

load_dotenv()
PINE_KEY = os.getenv("PINECONE_API_KEY")

print("Connecting to Pinecone...")
try:
    pc = Pinecone(api_key=PINE_KEY)
    index = pc.Index("teacherchronostwo")
    stats = index.describe_index_stats()
    print("Connected.")
    print(f"Total vectors: {stats.total_vector_count}")
except Exception as e:
    print(f"FAILED:\n{e}")