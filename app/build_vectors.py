import os

from .rag import TechSupportRAG


def main():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    csv_path = os.path.join(base_dir, "..", "tech_support_dataset.csv")
    cache_path = os.path.join(base_dir, "..", "tech_support_dataset.vectors.pkl")
    rag = TechSupportRAG(csv_path, cache_path=cache_path, require_cache=False)
    if rag.matrix is None:
        print("Failed to build vectors. Check the CSV file.")
        return
    print(f"Vectors saved to {cache_path}")


if __name__ == "__main__":
    main()
