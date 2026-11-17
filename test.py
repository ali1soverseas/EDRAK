import cohere

co = cohere.ClientV2(api_key="fsUVBD1Ul4oYf5MBZFzI5vtmrICHkq649pzZHIvw")

response = co.chat(
    messages=[{"role": "user", "content": "What is the capital of France?"}],
    thinking={
          "type": "enabled"
        },
    temperature=0.6,
    model="command-a-plus-05-2026",
)

print(response)