from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger


def paginate_queryset(queryset, page=1, limit=20):
    """
    Paginate a Django queryset and return data with pagination metadata.

    Args:
        queryset: Django QuerySet to paginate
        page: Current page number (default: 1)
        limit: Number of items per page (default: 20)

    Returns:
        dict: {
            'data': list of objects,
            'pagination': {
                'page': int,
                'limit': int,
                'total': int,
                'pages': int,
                'next': int or None,
                'previous': int or None,
                'has_next': bool,
                'has_previous': bool,
            }
        }
    """
    if page < 1:
        page = 1
    if limit < 1:
        limit = 20

    paginator = Paginator(queryset, limit)

    try:
        page_obj = paginator.page(page)
    except PageNotAnInteger:
        # If page is not an integer, deliver first page.
        page_obj = paginator.page(1)
    except EmptyPage:
        # If page is out of range (e.g. 9999), deliver last page of results.
        page_obj = paginator.page(paginator.num_pages)

    # Convert page_obj.object_list to list (if it's a QuerySet, it's already fine)
    data = list(page_obj.object_list)

    pagination = {
        "page": page_obj.number,
        "limit": limit,
        "page_size": limit,
        "total": paginator.count,
        "pages": paginator.num_pages,
        "next": page_obj.next_page_number() if page_obj.has_next() else None,
        "previous": (
            page_obj.previous_page_number() if page_obj.has_previous() else None
        ),
        "has_next": page_obj.has_next(),
        "has_previous": page_obj.has_previous(),
    }

    return {
        "data": data,
        "pagination": pagination,
    }
