import os
from huggingface_hub import InferenceClient

client = InferenceClient(
    provider="cohere",
    api_key=os.environ["HUGGINGFACE_TOKEN"],
)

prompt = """
Respond in a JSON format.
{
    "response": "Your response here"
}

Your specific request goes here:
Provide a funny one-line description of a cat.
"""

completion = client.chat.completions.create(
    model="CohereLabs/command-a-reasoning-08-2025",
    messages=[
        {
            "role": "user",
            "content": prompt
        }
    ],
)

print(completion.choices[0].message["content"])
print(completion.choices[0].message["reasoning_content"])
