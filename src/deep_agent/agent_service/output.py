from pydantic import BaseModel, Field
from pydantic_ai import ToolOutput

QUESTION_NAME = "deepagent_a2a_request_input"


class A2AQuestion(BaseModel):
    question: str = Field(min_length=1, max_length=2000)


def question_output():
    return ToolOutput(
        A2AQuestion, name=QUESTION_NAME,
        description="Pause the remote task and ask its caller for a required input. Use only when you cannot proceed.",
    )
