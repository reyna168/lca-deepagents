from dotenv import load_dotenv
load_dotenv()

import os
for k in ["LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2", "LANGSMITH_API_KEY",
          "LANGSMITH_PROJECT", "LANGSMITH_ENDPOINT", "LANGSMITH_WORKSPACE_ID"]:
    v = os.getenv(k)
    print(k, "=", (v[:8] + "...") if k == "LANGSMITH_API_KEY" and v else v)

from langsmith import Client, traceable
client = Client()
print("endpoint:", client.api_url)

@traceable(name="smith_test")
def hello(x):
    return x

hello("test")
client.flush()
print("done")