from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework import serializers, status

import logging

logger = logging.getLogger(__name__)

def _success(data: dict={}, message: str='success', status:int=status.HTTP_200_OK) -> Response:
    # logger.debug(f"Response: {data} Status: {status}")
    return Response({'status': True, 'message': message, 'data': data}, status=status)

def _error(data: list=[], message: str='error', status:int=status.HTTP_404_NOT_FOUND) -> Response:
    # logger.debug(f"Response: {data} Status: {status}")
    return Response({'status': False, 'message': message, 'data': data}, status=status)

class CustomPagination(PageNumberPagination):
    from rest_framework import status

    page_size = 10
    page_size_query_param = "page_size"
    max_page_size = 100

    def get_paginated_response(
        self,
        data=None,
        message=None,
        status=True,
        response_status=status.HTTP_200_OK,
        clean_pagination=False,
        pagination = None
    ):
        if data is None:
            data = []
        response_data = {
            "status": status,
            "message": message or "Success",
            "pagination": pagination or {
                "next": self.get_next_link(),
                "previous": self.get_previous_link(),
                "count": self.page.paginator.count,
                "current_page": self.page.number,
                "total_pages": self.page.paginator.num_pages,
                "page_size": self.page_size,
                'has_next': self.page.has_next(),
                'has_previous': self.page.has_previous(),
            },
            "data": data,
        }

        # Remove null values from pagination
        if clean_pagination:
            response_data["pagination"] = {
                k: v for k, v in response_data["pagination"].items() if v is not None
            }
        # logger.debug(response_data)
        return Response(response_data, status=response_status)

    def get_paginated_error(self, data=None, message=None, status=False):
        if data is None:
            data = []

        response_data = {
            "status": status,
            "message": message or "Error",
            "pagination": {
                "next": False,
                "previous": False,
                "count": 0,
                "current_page": 1,
                "total_pages": 0,
                "page_size": 0,
            },
            "data": data,
        }
        return Response(response_data)
    
    

class BasePaginatedSerializer(serializers.Serializer):
    """Pagination metadata structure from CustomPagination"""
    next = serializers.URLField(allow_null=True, required=False)
    previous = serializers.URLField(allow_null=True, required=False)
    count = serializers.IntegerField()
    current_page = serializers.IntegerField()
    total_pages = serializers.IntegerField()
    page_size = serializers.IntegerField()
