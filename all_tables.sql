SELECT
    SCHEMA_NAME(obj.schema_id) AS [Schema_Name],
    obj.name AS [Table_Or_View_Name],
    obj.type_desc AS [Object_Type],
    col.name AS [Column_Name],
    col.column_id AS [Column_Position],
    systypes.name AS [Data_Type],
    CASE 
        -- For types like nvarchar, show length in characters, not bytes
        WHEN systypes.name IN ('nchar', 'nvarchar') AND col.max_length <> -1 
            THEN CAST(col.max_length / 2 AS VARCHAR(10))
        WHEN col.max_length = -1 
            THEN 'MAX'
        ELSE CAST(col.max_length AS VARCHAR(10))
    END AS [Max_Length],
    -- Column definition (Default value or Computed column definition)
    ISNULL(def.definition, comp.definition) AS [Definition],
    -- Primary Key Indicator
    CAST(IIF(pk_idx.index_column_id IS NOT NULL, 1, 0) AS BIT) AS [Is_Primary_Key],
    -- Foreign Key Indicator
    CAST(IIF(fk.parent_column_id IS NOT NULL, 1, 0) AS BIT) AS [Is_Foreign_Key],
    -- Referenced Foreign Key Details
    OBJECT_SCHEMA_NAME(fk.referenced_object_id) AS [FK_Referenced_Schema],
    OBJECT_NAME(fk.referenced_object_id) AS [FK_Referenced_Table],
    ref_col.name AS [FK_Referenced_Column]
FROM
    sys.objects AS obj
INNER JOIN
    sys.columns AS col ON obj.object_id = col.object_id
INNER JOIN 
    sys.types AS systypes ON col.user_type_id = systypes.user_type_id
-- Default constraint definitions
LEFT JOIN 
    sys.default_constraints AS def ON col.default_object_id = def.object_id AND col.object_id = def.parent_object_id
-- Computed column definitions
LEFT JOIN 
    sys.computed_columns AS comp ON col.object_id = comp.object_id AND col.column_id = comp.column_id
-- Primary Key Check
LEFT JOIN (
    SELECT 
        ic.object_id, 
        ic.column_id, 
        ic.index_column_id
    FROM sys.indexes AS i
    INNER JOIN sys.index_columns AS ic 
        ON i.object_id = ic.object_id AND i.index_id = ic.index_id
    WHERE i.is_primary_key = 1
) AS pk_idx ON col.object_id = pk_idx.object_id AND col.column_id = pk_idx.column_id
-- Foreign Key Check
LEFT JOIN 
    sys.foreign_key_columns AS fk ON col.object_id = fk.parent_object_id AND col.column_id = fk.parent_column_id
-- Referenced Table Details
LEFT JOIN 
    sys.columns AS ref_col ON fk.referenced_object_id = ref_col.object_id AND fk.referenced_column_id = ref_col.column_id
WHERE
    obj.type IN ('U', 'V') -- U = User Table, V = View
ORDER BY
    Schema_Name,
    Table_Or_View_Name,
    Column_Position;
