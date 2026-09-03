"""
Libraries API wrapper for ADS.

Provides access to personal library management:
- Create, read, update, delete libraries
- Add/remove documents
- Permissions management
- Set operations (union, intersection, difference)
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from ..client import BaseClient

__all__ = ["LibrariesAPI", "Library"]


@dataclass
class Library:
    """Container for library metadata and documents."""

    id: str
    """Unique library identifier."""

    name: str
    """Library name."""

    description: str = ""
    """Library description."""

    num_documents: int = 0
    """Number of documents in the library."""

    public: bool = False
    """Whether the library is publicly accessible."""

    permission: str = ""
    """User's permission level: 'owner', 'admin', 'write', 'read'."""

    owner: str = ""
    """Library owner identifier."""

    date_created: str | None = None
    """ISO date when library was created."""

    date_last_modified: str | None = None
    """ISO date when library was last modified."""

    documents: list[str] = field(default_factory=list)
    """List of bibcodes in the library."""

    @classmethod
    def from_response(cls, data: dict) -> "Library":
        """Create Library from API response."""
        # Handle both list item and full library responses
        metadata = data.get("metadata", data)
        docs = data.get("documents", [])

        return cls(
            id=metadata.get("id", ""),
            name=metadata.get("name", ""),
            description=metadata.get("description", ""),
            num_documents=metadata.get("num_documents", len(docs)),
            public=metadata.get("public", False),
            permission=metadata.get("permission", ""),
            owner=metadata.get("owner", ""),
            date_created=metadata.get("date_created"),
            date_last_modified=metadata.get("date_last_modified"),
            documents=docs,
        )


class LibrariesAPI:
    """
    Libraries API for managing personal ADS libraries.

    Examples
    --------
    >>> api = LibrariesAPI(client)
    >>> libs = api.list_libraries()
    >>> lib = api.get_library(libs[0].id)
    >>> print(lib.documents)
    """

    def __init__(self, client: "BaseClient"):
        self._client = client

    def list_libraries(
        self,
        start: int = 0,
        rows: int = 100,
        sort: Literal["date_created", "date_last_modified"] = "date_created",
        order: Literal["asc", "desc"] = "asc",
    ) -> list[Library]:
        """
        List all libraries for the authenticated user.

        Parameters
        ----------
        start : int, optional
            Starting index for pagination.
        rows : int, optional
            Number of libraries to return.
        sort : str, optional
            Sort field: "date_created" or "date_last_modified".
        order : str, optional
            Sort order: "asc" or "desc".

        Returns
        -------
        list[Library]
            List of library metadata.
        """
        params = {
            "start": start,
            "rows": rows,
            "sort": sort,
            "order": order,
        }
        response = self._client.get("/biblib/libraries", params=params)
        return [Library.from_response(lib) for lib in response.get("libraries", [])]

    def get_library(
        self,
        library_id: str,
        raw: bool = False,
        start: int = 0,
        rows: int = 2000,
    ) -> Library:
        """
        Get a specific library with its documents.

        Parameters
        ----------
        library_id : str
            Unique library identifier.
        raw : bool, optional
            If True, return exact bibcodes even if not in ADS.
        start : int, optional
            Starting index for document pagination.
        rows : int, optional
            Number of documents to return.

        Returns
        -------
        Library
            Library with full document list.
        """
        params = {"start": start, "rows": rows}
        if raw:
            params["raw"] = "true"

        response = self._client.get(f"/biblib/libraries/{library_id}", params=params)
        return Library.from_response(response)

    def create_library(
        self,
        name: str,
        description: str = "My ADS library",
        public: bool = False,
        bibcodes: list[str] | None = None,
    ) -> Library:
        """
        Create a new library.

        Parameters
        ----------
        name : str
            Library name (must be unique for user).
        description : str, optional
            Library description.
        public : bool, optional
            Make library publicly accessible.
        bibcodes : list[str], optional
            Initial bibcodes to add.

        Returns
        -------
        Library
            Created library metadata.
        """
        payload = {
            "name": name,
            "description": description,
            "public": public,
        }
        if bibcodes:
            payload["bibcode"] = bibcodes

        response = self._client.post("/biblib/libraries", data=payload)
        return Library.from_response(response)

    def update_library(
        self,
        library_id: str,
        name: str | None = None,
        description: str | None = None,
        public: bool | None = None,
    ) -> dict:
        """
        Update library metadata.

        Parameters
        ----------
        library_id : str
            Library identifier.
        name : str, optional
            New library name.
        description : str, optional
            New description.
        public : bool, optional
            New public status.

        Returns
        -------
        dict
            Update response.
        """
        payload = {}
        if name is not None:
            payload["name"] = name
        if description is not None:
            payload["description"] = description
        if public is not None:
            payload["public"] = public

        return self._client.put(f"/biblib/documents/{library_id}", data=payload)

    def delete_library(self, library_id: str) -> bool:
        """
        Delete a library.

        Parameters
        ----------
        library_id : str
            Library identifier.

        Returns
        -------
        bool
            True if successful.
        """
        self._client.delete(f"/biblib/documents/{library_id}")
        return True

    def add_documents(self, library_id: str, bibcodes: list[str]) -> dict:
        """
        Add documents to a library.

        Parameters
        ----------
        library_id : str
            Library identifier.
        bibcodes : list[str]
            Bibcodes to add.

        Returns
        -------
        dict
            Response with number added.
        """
        payload = {
            "bibcode": bibcodes,
            "action": "add",
        }
        return self._client.post(f"/biblib/documents/{library_id}", data=payload)

    def remove_documents(self, library_id: str, bibcodes: list[str]) -> dict:
        """
        Remove documents from a library.

        Parameters
        ----------
        library_id : str
            Library identifier.
        bibcodes : list[str]
            Bibcodes to remove.

        Returns
        -------
        dict
            Response with number removed.
        """
        payload = {
            "bibcode": bibcodes,
            "action": "remove",
        }
        return self._client.post(f"/biblib/documents/{library_id}", data=payload)

    def operation(
        self,
        primary_id: str,
        action: Literal["union", "intersection", "difference", "copy", "empty"],
        secondary_ids: list[str] | None = None,
        name: str | None = None,
        description: str | None = None,
        public: bool = False,
    ) -> Library:
        """
        Perform set operations on libraries.

        Parameters
        ----------
        primary_id : str
            Primary library identifier.
        action : str
            Operation: "union", "intersection", "difference", "copy", "empty".
        secondary_ids : list[str], optional
            Secondary library IDs (not needed for "empty").
        name : str, optional
            Name for new library (for union/intersection/difference).
        description : str, optional
            Description for new library.
        public : bool, optional
            Make new library public.

        Returns
        -------
        Library
            Resulting library.
        """
        payload = {"action": action}

        if secondary_ids:
            payload["libraries"] = secondary_ids
        if name:
            payload["name"] = name
        if description:
            payload["description"] = description
        if public:
            payload["public"] = public

        response = self._client.post(
            f"/biblib/libraries/operations/{primary_id}", data=payload
        )
        return Library.from_response(response)

    def union(
        self,
        primary_id: str,
        secondary_ids: list[str],
        name: str | None = None,
    ) -> Library:
        """Create union of libraries."""
        return self.operation(primary_id, "union", secondary_ids, name=name)

    def intersection(
        self,
        primary_id: str,
        secondary_ids: list[str],
        name: str | None = None,
    ) -> Library:
        """Create intersection of libraries."""
        return self.operation(primary_id, "intersection", secondary_ids, name=name)

    def difference(
        self,
        primary_id: str,
        secondary_ids: list[str],
        name: str | None = None,
    ) -> Library:
        """Create difference of libraries (primary - secondary)."""
        return self.operation(primary_id, "difference", secondary_ids, name=name)

    def empty(self, library_id: str) -> Library:
        """Empty a library of all its contents."""
        return self.operation(library_id, "empty")
