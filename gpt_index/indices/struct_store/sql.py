"""SQL Structured Store."""
import json
from typing import Any, Dict, Optional, Sequence

from gpt_index.data_structs.node_v2 import Node
from gpt_index.data_structs.table_v2 import SQLStructTable
from gpt_index.indices.base import BaseGPTIndex, QueryMap
from gpt_index.indices.common.struct_store.schema import SQLContextContainer
from gpt_index.indices.common.struct_store.sql import SQLStructDatapointExtractor
from gpt_index.indices.query.schema import QueryMode
from gpt_index.indices.service_context import ServiceContext
from gpt_index.indices.struct_store.base import BaseGPTStructStoreIndex
from gpt_index.indices.struct_store.container_builder import SQLContextContainerBuilder
from gpt_index.indices.struct_store.sql_query import (
    GPTNLStructStoreIndexQuery,
    GPTSQLStructStoreIndexQuery,
)
from gpt_index.langchain_helpers.sql_wrapper import SQLDatabase
from sqlalchemy import Table


class GPTSQLStructStoreIndex(BaseGPTStructStoreIndex[SQLStructTable]):
    """Base GPT SQL Struct Store Index.

    The GPTSQLStructStoreIndex is an index that uses a SQL database
    under the hood. During index construction, the data can be inferred
    from unstructured documents given a schema extract prompt,
    or it can be pre-loaded in the database.

    During query time, the user can either specify a raw SQL query
    or a natural language query to retrieve their data.

    Args:
        documents (Optional[Sequence[DOCUMENTS_INPUT]]): Documents to index.
            NOTE: in the SQL index, this is an optional field.
        sql_database (Optional[SQLDatabase]): SQL database to use,
            including table names to specify.
            See :ref:`Ref-Struct-Store` for more details.
        table_name (Optional[str]): Name of the table to use
            for extracting data.
            Either table_name or table must be specified.
        table (Optional[Table]): SQLAlchemy Table object to use.
            Specifying the Table object explicitly, instead of
            the table name, allows you to pass in a view.
            Either table_name or table must be specified.
        sql_context_container (Optional[SQLContextContainer]): SQL context container.
            an be generated from a SQLContextContainerBuilder.
            See :ref:`Ref-Struct-Store` for more details.

    """

    index_struct_cls = SQLStructTable

    def __init__(
        self,
        nodes: Optional[Sequence[Node]] = None,
        index_struct: Optional[SQLStructTable] = None,
        service_context: Optional[ServiceContext] = None,
        sql_database: Optional[SQLDatabase] = None,
        table_name: Optional[str] = None,
        table: Optional[Table] = None,
        ref_doc_id_column: Optional[str] = None,
        sql_context_container: Optional[SQLContextContainer] = None,
        **kwargs: Any,
    ) -> None:
        """Initialize params."""
        if sql_database is None:
            raise ValueError("sql_database must be specified")
        self.sql_database = sql_database
        # needed here for data extractor
        self._ref_doc_id_column = ref_doc_id_column
        self._table_name = table_name
        self._table = table

        # if documents aren't specified, pass in a blank []
        if index_struct is None:
            nodes = nodes or []

        super().__init__(
            nodes=nodes,
            index_struct=index_struct,
            service_context=service_context,
            **kwargs,
        )

        # TODO: index_struct context_dict is deprecated,
        # we're migrating storage of information to here.
        if sql_context_container is None:
            container_builder = SQLContextContainerBuilder(sql_database)
            sql_context_container = container_builder.build_context_container()
        self.sql_context_container = sql_context_container

    def _build_index_from_nodes(self, nodes: Sequence[Node]) -> SQLStructTable:
        """Build index from nodes."""
        index_struct = self.index_struct_cls()
        if len(nodes) == 0:
            return index_struct
        else:
            data_extractor = SQLStructDatapointExtractor(
                self._service_context.llm_predictor,
                self.schema_extract_prompt,
                self.output_parser,
                self.sql_database,
                table_name=self._table_name,
                table=self._table,
                ref_doc_id_column=self._ref_doc_id_column,
            )
            for node in nodes:
                data_extractor.insert_datapoint_from_nodes([node])
        return index_struct

    def _insert(self, nodes: Sequence[Node], **insert_kwargs: Any) -> None:
        """Insert a document."""
        data_extractor = SQLStructDatapointExtractor(
            self._service_context.llm_predictor,
            self.schema_extract_prompt,
            self.output_parser,
            self.sql_database,
            table_name=self._table_name,
            table=self._table,
            ref_doc_id_column=self._ref_doc_id_column,
        )
        data_extractor.insert_datapoint_from_nodes(nodes)

    @classmethod
    def get_query_map(self) -> QueryMap:
        """Get query map."""
        return {
            QueryMode.DEFAULT: GPTNLStructStoreIndexQuery,
            QueryMode.SQL: GPTSQLStructStoreIndexQuery,
        }

    def _preprocess_query(self, mode: QueryMode, query_kwargs: Any) -> None:
        """Preprocess query.

        This allows subclasses to pass in additional query kwargs
        to query, for instance arguments that are shared between the
        index and the query class. By default, this does nothing.
        This also allows subclasses to do validation.

        """
        super()._preprocess_query(mode, query_kwargs)
        # pass along sql_database, table_name
        query_kwargs["sql_database"] = self.sql_database
        if "sql_context_container" not in query_kwargs:
            query_kwargs["sql_context_container"] = self.sql_context_container
        if mode == QueryMode.DEFAULT:
            query_kwargs["ref_doc_id_column"] = self._ref_doc_id_column

    @classmethod
    def load_from_string(cls, index_string: str, **kwargs: Any) -> "BaseGPTIndex":
        """Load index from string (in JSON-format).

        This method loads the index from a JSON string. The index data
        structure itself is preserved completely. If the index is defined over
        subindices, those subindices will also be preserved (and subindices of
        those subindices, etc.).

        NOTE: load_from_string should not be used for indices composed on top
        of other indices. Please define a `ComposableGraph` and use
        `save_to_string` and `load_from_string` on that instead.

        Args:
            index_string (str): The index string (in JSON-format).

        Returns:
            BaseGPTIndex: The loaded index.

        """
        # NOTE: also getting deserialized in parent class,
        # figure out how to deal with later
        result_dict = json.loads(index_string)
        sql_context_container = SQLContextContainer.from_dict(
            result_dict["sql_context_container"]
        )
        result_obj = super().load_from_string(
            index_string, sql_context_container=sql_context_container, **kwargs
        )
        return result_obj

    def save_to_string(self, **save_kwargs: Any) -> str:
        """Save to string.

        This method stores the index into a JSON string.

        NOTE: save_to_string should not be used for indices composed on top
        of other indices. Please define a `ComposableGraph` and use
        `save_to_string` and `load_from_string` on that instead.

        Returns:
            str: The JSON string of the index.

        """
        out_dict: Dict[str, Any] = {
            "index_id": self.index_struct.index_id,
            "docstore": self.docstore.to_dict(),
            "sql_context_container": self.sql_context_container.to_dict(),
        }
        return json.dumps(out_dict, **save_kwargs)
