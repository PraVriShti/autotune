import logging

from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.generics import ListAPIView, RetrieveAPIView, UpdateAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from .celery_task import process_task
from .models import Examples, Task, WorkflowConfig, Workflows
from .serializers import (
    PromptSerializer,
    UserSerializer,
    WorkflowConfigSerializer,
    WorkflowSerializer,
)
from .task import DataFetcher
from .utils import dehydrate_cache

logger = logging.getLogger(__name__)


def index():
    return HttpResponse("Hello, world. You're at the workflow index.")


@csrf_exempt
@api_view(["POST"])
def create_workflow_with_prompt(request):
    """
    Creates a new workflow and its associated prompt
    Parameters:
    - request (HttpRequest): The HTTP request object containing the JSON payload.

    Request JSON payload format:
    {
        "workflow": {
            "workflow_name": "Data Analysis Workflow",
            "workflow_type": "QnA",
            "total_examples": 1000,
            "split": [
                70,
                20,
                10
            ],
            "llm_model": "gpt-4-0125-preview",
            "cost": 200,
            "tags": [
                "data analysis",
                "machine learning"
            ],
            "user": "429088bd-73c4-454a-91c7-e29081b36531"
        },
        "user_prompt": "Create questions on world war 2 for class 8 students",
        "examples": [
            {
                "text": "Example question about data analysis?",
                "label": "positive",
                "reason": "Relevant to the domain of data analysis"
            },
            {
                "text": "Example question not related to data analysis.",
                "label": "negative",
                "reason": "Not relevant to the domain"
            }
            // Additional examples can be added here (This is Optional)
        ]
    }

    Returns:
        {
          "workflow": {
            "workflow_id": "123e4567-e89b-12d3-a456-426614174000",
            "workflow_name": "Data Analysis Workflow",
            "total_examples": 1000,
            "split": [70, 20, 10],
            "llm_model": "gpt-4-0125-preview",
            "cost": 200,
            "tags": ["data analysis", "machine learning"],
            "user": "uuid-of-the-user",
            "created_at": "2024-03-07T12:00:00Z",
            "updated_at": "2024-03-07T12:00:00Z"
          },
          "user_prompt": "User provided information to replace {{.DocumentChunk}}",
          "examples": [ // this is Optional
            {
                "example_id": "456f7890-f123-45h6-i789-012j345678k9",
                "text": "Example question about data analysis?",
                "label": "positive",
                "reason": "Relevant to the domain of data analysis",
                "workflow": "123e4567-e89b-12d3-a456-426614174000"
            },
            // Additional examples if provided
          ]
        }
    """

    with transaction.atomic():
        workflow_serializer = WorkflowSerializer(data=request.data.get("workflow", {}))
        if workflow_serializer.is_valid(raise_exception=True):
            workflow = workflow_serializer.save()

            prompt_data = {
                "user": request.data.get("user_prompt", ""),
                "workflow": workflow.pk,
            }

            prompt_serializer = PromptSerializer(data=prompt_data)
            if prompt_serializer.is_valid(raise_exception=True):
                prompt_serializer.save()

                return Response(
                    {
                        "workflow": workflow_serializer.data,
                        "prompt": prompt_serializer.data,
                    },
                    status=status.HTTP_201_CREATED,
                )

    return Response(
        {
            "error": "Invalid data for workflow or prompt",
        },
        status=status.HTTP_400_BAD_REQUEST,
    )


@api_view(["POST"])
def iterate_workflow(request, workflow_id):
    """
    Iterates over a workflow by either adding new examples or refining existing ones based on the provided data.
    This operation can generate or refine questions and answers based on the examples associated with the workflow.

    Args:
        request (HttpRequest): The request object containing 'examples' data.
        workflow_id (int): The ID of the workflow to be iterated on.

    Sample Request Payload:
        {
            "examples": [
                {
                    "text": "What is AI?",
                    "label": "positive",
                    "reason": "Relevant to the field of study"
                },
                {
                    "text": "What is 2 + 2?",
                    "label": "negative",
                    "reason": "Irrelevant question"
                }
            ]
        }
    Returns:
    - A response object with the outcome of the iteration process. The response structure and data depend on the json schema defined in the configfunction.
    """
    workflow = get_object_or_404(Workflows, pk=workflow_id)
    examples_exist = Examples.objects.filter(
        workflow_id=workflow_id, label__isnull=False
    ).exists()

    if "examples" in request.data:
        for example_data in request.data["examples"]:
            text = example_data.get("text")
            label = example_data.get("label", "")
            reason = example_data.get("reason", "")

            example, created = Examples.objects.get_or_create(
                workflow=workflow,
                text=text,
                defaults={"label": label, "reason": reason},
            )

            if not created:
                example.label = label
                example.reason = reason
                example.save()

    fetcher = DataFetcher()
    response = fetcher.generate_or_refine(
        workflow_id=workflow.workflow_id,
        total_examples=workflow.total_examples,
        workflow_type=workflow.workflow_type,
        llm_model=workflow.llm_model,
        refine=examples_exist,
    )
    return Response(response)


class WorkflowDetailView(RetrieveAPIView):
    """
    Retrieves details of a specific workflow by its unique 'workflow_id'.

    Args:
        - workflow_id (UUID): The unique identifier of the workflow to retrieve.
          It is part of the URL pattern and should be provided in the request URL.

    Returns:
        {
        "workflow": {
            "workflow_id": "123e4567-e89b-12d3-a456-426614174000",
            "workflow_name": "Data Analysis Workflow",
            "total_examples": 1000,
            "split": [70, 20, 10],
            "llm_model": "gpt-4-0125-preview",
            "cost": 200,
            "tags": ["data analysis", "machine learning"],
            "user": "uuid-of-the-user",
            "created_at": "2024-03-07T12:00:00Z",
            "updated_at": "2024-03-07T12:00:00Z",
            "prompt": {
                "id": "789e4567-e89b-12d3-a456-426614174999",
                "text": "Generate insights from the given dataset.",
                "parameters": {
                    "max_tokens": 150,
                    "temperature": 0.5
                },
                "created_at": "2024-03-07T12:00:00Z",
                "updated_at": "2024-03-07T12:00:00Z",
                "workflow": "123e4567-e89b-12d3-a456-426614174000"
            }
        }
    }
    """

    queryset = Workflows.objects.all()
    serializer_class = WorkflowSerializer
    lookup_field = "workflow_id"


@api_view(["PATCH"])
def update_prompt(request, workflow_id):
    """
    Updates the user prompt or source for a given workflow's prompt.

    Args:
        request (HttpRequest): HTTP request with the 'user' and/or 'source' fields to update the prompt.
        workflow_id (int): ID of the workflow whose prompt is to be updated.

    Request Payload Example:
    {
        "user": "Updated user prompt text.",
        "source": "Updated source text."
    }

    Returns:
        - HTTP 200 OK: On successful update with the updated prompt data.
        - HTTP 404 Not Found: If no workflow with the given ID exists.

    Sample Response Payload:
        {
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-02T00:00:00Z",
            "user": "Updated user prompt text.",
            "source": "Updated source text.",
            "workflow": 1
        }
    """
    workflow = get_object_or_404(Workflows, pk=workflow_id)
    prompt = workflow.prompt

    user_prompt = request.data.get("user")
    source = request.data.get("source")

    if user_prompt is not None:
        prompt.user = user_prompt

    if source is not None:
        prompt.source = source

    prompt.save()
    return Response(PromptSerializer(prompt).data)


@api_view(["GET"])
def retrieve_prompt(request, workflow_id):
    """
    Retrieves the prompt for a given workflow.

    Args:
        request (HttpRequest): The HTTP request object.
        workflow_id (int): ID of the workflow whose prompt is to be retrieved.

    Returns:
        - HTTP 200 OK: With the prompt data.
        - HTTP 404 Not Found: If no workflow with the given ID exists.

     Sample Response Payload:
        {
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-02T00:00:00Z",
            "user": "Updated user prompt text.",
            "source": "Updated source text.",
            "workflow": 1
        }
    """
    workflow = get_object_or_404(Workflows, pk=workflow_id)
    prompt = workflow.prompt
    return Response(PromptSerializer(prompt).data)


class WorkflowUpdateView(UpdateAPIView):
    """
    Update an existing workflow.

    PUT /workflow/{workflow_id}/update/

    Parameters:
    - workflow_id (URL Path): ID of the workflow to be updated.

    Request Body (application/json):
    {
        "workflow_name": "New Workflow Name",
        "total_examples": 1200,
        ...
    }

    Responses:
    - 200 OK: Workflow successfully updated.
      {
          "workflow_name": "New Workflow Name",
          "total_examples": 1200,
          ...
      }
    - 404 Not Found: If no workflow with the given ID exists.
    """

    queryset = Workflows.objects.all()
    serializer_class = WorkflowSerializer
    lookup_field = "workflow_id"


class WorkflowDuplicateView(APIView):
    """
    Duplicate an existing workflow, creating a new instance with a new ID.

    PUT /workflow/{workflow_id}/duplicate/

    Parameters:
    - workflow_id (URL Path): ID of the workflow to be duplicated.

    Responses:
    - 201 Created: Workflow successfully duplicated.
      {
          "workflow_id": "new-workflow-id",
          ...
      }
    - 404 Not Found: If no workflow with the given ID exists.
    """

    def put(self, request, workflow_id):
        workflow = get_object_or_404(Workflows, workflow_id=workflow_id)
        workflow.pk = None
        workflow.save()
        serializer = WorkflowSerializer(workflow)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class WorkflowStatusView(APIView):
    """
    Retrieve the status of a specific workflow.

    GET /workflow/status/{workflow_id}/

    Parameters:
    - workflow_id (URL Path): ID of the workflow whose status is to be retrieved.

    Responses:
    - 200 OK: Successfully retrieved the status of the workflow.
      {
          "workflow_id": "workflow-id",
          "status": "Workflow Status"
      }
    - 404 Not Found: If no workflow with the given ID exists.
    """

    def get(self, request, workflow_id):
        workflow = get_object_or_404(Workflows, workflow_id=workflow_id)
        return Response({"status": workflow.status})


class WorkflowSearchView(ListAPIView):
    """
    Search for workflows by tag or name.

    GET /workflow/q/?tags=tag1,tag2

    Query Parameters:
    - tags (string): Comma-separated list of tags to filter workflows by.

    Responses:
    - 200 OK: Returns a list of workflows that match the search criteria.
      [
          {
              "workflow_id": "some-workflow-id",
              "workflow_name": "Some Workflow Name",
              ...
          },
          ...
      ]
    """

    serializer_class = WorkflowSerializer

    def get_queryset(self):
        tags_param = self.request.query_params.get("tags", "")
        tags_query = tags_param.split(",") if tags_param else []
        query = Q(tags__overlap=tags_query) if tags_query else Q()
        return Workflows.objects.filter(query)


class TaskProgressView(APIView):
    """
    Get the progress of tasks associated with a specific workflow.

    GET /progress/<workflow_id>/

    Path Parameters:
    - workflow_id (UUID): The unique identifier of the workflow to retrieve task progress for.

    Responses:
    - 200 OK: Returns the progress of tasks for the specified workflow.
      {
          "workflow_id": "some-workflow-id",
          "progress": "75%"
      }
    - 404 Not Found: No tasks found for this workflow or the workflow does not exist.
    - 500 Internal Server Error: A server error occurred.
    """

    def get(self, request, workflow_id, *args, **kwargs):
        try:
            # Filter only main tasks (no parent_task)
            main_tasks = Task.objects.filter(
                workflow_id=workflow_id, parent_task__isnull=True
            )
            if not main_tasks.exists():
                return JsonResponse(
                    {"error": "No tasks found for this workflow"}, status=404
                )

            total_main_tasks = main_tasks.count()
            # Assuming 'Completed' status means all subtasks are also completed
            completed_main_tasks = main_tasks.filter(status="Completed").count()

            progress_percent = (completed_main_tasks / total_main_tasks) * 100

            return JsonResponse(
                {"workflow_id": workflow_id, "progress": f"{progress_percent}%"},
                status=200,
            )
        except Exception as e:
            return JsonResponse({"error": str(e)}, status=500)


@api_view(["PUT"])
def generate_task(request, workflow_id, *args, **kwargs):
    try:
        workflow = Workflows.objects.get(workflow_id=workflow_id)
    except Workflows.DoesNotExist:
        return JsonResponse({"error": "Workflow not found"}, status=404)
    task = Task.objects.create(
        name=f"Batch Task for Workflow {workflow_id}",
        status="Starting",
        workflow=workflow,
    )

    process_task.delay(task.id)

    return JsonResponse(
        {"message": "Tasks creation initiated", "task_id": task.id}, status=202
    )


@api_view(["GET"])
def dehydrate_cache_view(request, key_pattern):
    """
    A simple view to dehydrate cache entries based on a key pattern.
    """
    dehydrate_cache(key_pattern)
    return JsonResponse(
        {"status": "success", "message": "Cache dehydrated successfully."}
    )


@api_view(["POST"])
def create_workflow_config(request):
    serializer = WorkflowConfigSerializer(data=request.data)

    if serializer.is_valid():
        serializer.save()
        return Response(
            {
                "message": "Workflow config created successfully!",
                "config": serializer.data,
            },
            status=status.HTTP_201_CREATED,
        )
    else:
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(["PATCH"])
def update_workflow_config(request, config_id):
    config = WorkflowConfig.objects.get(id=config_id)
    serializer = WorkflowConfigSerializer(config, data=request.data, partial=True)

    if serializer.is_valid():
        serializer.save()
        return Response(serializer.data)
    else:
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(["POST"])
def add_user(request):
    serializer = UserSerializer(data=request.data)

    if serializer.is_valid():
        serializer.save()
        return Response(
            {"message": "User created successfully!", "user": serializer.data},
            status=status.HTTP_201_CREATED,
        )
    else:
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(["POST"])
def train(request):
    # TBD
    return JsonResponse({"message": "hey"})


# what is task table
# how to send prompt to llm on iteration
# what is model and llm model in workflow
# data.py for langchain
# should prompt created with workflow or independent too. or maybe update separately?