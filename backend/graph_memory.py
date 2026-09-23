# graph_memory.py
import networkx as nx
import json
import os
import threading
from config import (
    GRAPH_DATABASE_PATH, GRAPH_ENTITY_CAREER, GRAPH_ENTITY_FAMILY,
    GRAPH_ENTITY_LIFESTYLE, GRAPH_MIN_WEIGHT_THRESHOLD
)

graph_lock = threading.Lock()
graph = nx.Graph()

# Initialize base directory structure safely
os.makedirs(os.path.dirname(GRAPH_DATABASE_PATH), exist_ok=True)

if os.path.exists(GRAPH_DATABASE_PATH):
    try:
        with open(GRAPH_DATABASE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            graph = nx.node_link_graph(data)
    except Exception:
        pass

def extract_entities_and_update(user_input):
    """Maps continuous contextual references into persistent nodes."""
    with graph_lock:
        text = user_input.lower()
        
        # Ensure baseline Ego network nodes exist
        if not graph.has_node("User"):
            graph.add_node("User", type="core")
        if not graph.has_node("Rosia"):
            graph.add_node("Rosia", type="core")

        # Dynamic target routing (from config)
        entities = {
            "career/builder": GRAPH_ENTITY_CAREER,
            "family/legacy": GRAPH_ENTITY_FAMILY,
            "lifestyle": GRAPH_ENTITY_LIFESTYLE
        }

        for target_node, keywords in entities.items():
            if any(w in text for w in keywords):
                if not graph.has_node(target_node):
                    graph.add_node(target_node, type="context")
                
                # Increment edge connection weights dynamically
                weight = 1
                if graph.has_edge("User", target_node):
                    weight = graph["User"][target_node].get("weight", 0) + 1
                graph.add_edge("User", target_node, weight=weight)

        # Persist snapshot directly to storage
        try:
            data = nx.node_link_data(graph)
            with open(GRAPH_DATABASE_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

def get_graph_context():
    """Generates structural identity string for the LLM prompt context."""
    with graph_lock:
        if len(graph.nodes) <= 2:
            return ""
        
        statements = []
        for edge in graph.edges(data=True):
            if edge[0] == "User" and edge[2].get("weight", 0) > GRAPH_MIN_WEIGHT_THRESHOLD:
                statements.append(f"User frequently discusses {edge[1]}")
                
        return "; ".join(statements[:3])