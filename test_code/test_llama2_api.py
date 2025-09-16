from huggingface_hub import InferenceClient
import json
import os

# Replace with your actual Hugging Face API token
# For security, you can set this as an environment variable
HUGGINGFACE_TOKEN = os.environ.get("HUGGINGFACE_TOKEN", "YOUR_HF_TOKEN")
print(HUGGINGFACE_TOKEN)
# Initialize the InferenceClient with the model and your token
# You can find the model name (e.g., "meta-llama/Meta-Llama-3-8B-Instruct")
# on the Hugging Face Hub.

client = InferenceClient(
    model="meta-llama/Meta-Llama-3-8B-Instruct",
    token=HUGGINGFACE_TOKEN
)

# Define the prompt. For structured output like JSON,
# you must explicitly instruct the model in the prompt itself.
prompt = """
Respond in a JSON format.
{
    "response": "Your response here"
}

Your specific request goes here:
Provide a funny one-line description of a cat.
"""

# Call the text generation API
try:
    response = client.text_generation(
        prompt=prompt,
        temperature=0.2,
        top_p=0.8,
        max_new_tokens=512
    )

    # Parse the JSON string from the response text
    json_response_string = response
    parsed_json = json.loads(json_response_string)

    print(json.dumps(parsed_json, indent=4))

except Exception as e:
    print(f"Error: {e}")
    print("If you receive a '401 Unauthorized' error, please check that your Hugging Face token is correct and valid.")


"""
import os
from huggingface_hub import InferenceClient

client = InferenceClient(
    provider="featherless-ai",
    api_key=os.environ["HF_TOKEN"],
)

result = client.text_generation(
    "Can you please let us know more details about your ",
    model="meta-llama/Llama-3.1-8B",
)
"""