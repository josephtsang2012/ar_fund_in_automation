-- 清理：如果临时表已存在，先删除（确保可反复执行）
-- ============================================
IF OBJECT_ID('tempdb..#TablesWithData') IS NOT NULL
    DROP TABLE #TablesWithData;


-- ============================================
-- 第一步：找出所有在 13:15~13:30 之间有更新的表
-- ============================================

DECLARE @SQL NVARCHAR(MAX) = '';
DECLARE @TableName NVARCHAR(255);
DECLARE @SchemaName NVARCHAR(255);

-- 创建临时表，存放"有更新的表名"
CREATE TABLE #TablesWithData (
    表名 NVARCHAR(255)
);

-- 声明游标，遍历所有包含 UPDATED_DTE 字段的表
DECLARE TableCursor CURSOR FOR
SELECT 
    t.TABLE_SCHEMA,
    t.TABLE_NAME
FROM INFORMATION_SCHEMA.TABLES t
INNER JOIN INFORMATION_SCHEMA.COLUMNS c 
    ON t.TABLE_NAME = c.TABLE_NAME 
    AND t.TABLE_SCHEMA = c.TABLE_SCHEMA
WHERE t.TABLE_TYPE = 'BASE TABLE'
  AND c.COLUMN_NAME = 'UPDATED_DTE'
  AND t.TABLE_NAME NOT LIKE 'sys%'
ORDER BY t.TABLE_NAME;

OPEN TableCursor;
FETCH NEXT FROM TableCursor INTO @SchemaName, @TableName;

-- 循环每个表，检查是否有数据在时间范围内
WHILE @@FETCH_STATUS = 0
BEGIN
    SET @SQL = '
        IF EXISTS (
            SELECT 1 
            FROM ' + QUOTENAME(@SchemaName) + '.' + QUOTENAME(@TableName) + '
            WHERE UPDATED_DTE >= ''2026-07-22 13:15:00'' 
              AND UPDATED_DTE <= ''2026-07-22 13:30:00''
        )
        BEGIN
            INSERT INTO #TablesWithData VALUES (''' + @SchemaName + '.' + @TableName + ''');
        END
    ';
    
    EXEC sp_executesql @SQL;
    
    FETCH NEXT FROM TableCursor INTO @SchemaName, @TableName;
END

CLOSE TableCursor;
DEALLOCATE TableCursor;

SELECT * FROM #TablesWithData;

-- ============================================
-- 第二步：用查到的表名，去查每个表的具体数据
-- ============================================
DECLARE @FullTableName NVARCHAR(255);
DECLARE @SchemaName2 NVARCHAR(255);
DECLARE @TableName2 NVARCHAR(255);
DECLARE @SQL2 NVARCHAR(MAX) = '';

DECLARE TableCursor2 CURSOR FOR
SELECT 表名 FROM #TablesWithData ORDER BY 表名;

OPEN TableCursor2;
FETCH NEXT FROM TableCursor2 INTO @FullTableName;

WHILE @@FETCH_STATUS = 0
BEGIN
    SET @SchemaName2 = PARSENAME(@FullTableName, 2);
    SET @TableName2 = PARSENAME(@FullTableName, 1);
    
    -- 【修复点】把 AS 表名 改成 AS TableName（不用中文）
    SET @SQL2 = '
        SELECT 
            ''' + @FullTableName + ''' AS TableName,
            *
        FROM ' + QUOTENAME(@SchemaName2) + '.' + QUOTENAME(@TableName2) + '
        WHERE UPDATED_DTE >= ''2026-07-22 13:15:00'' 
          AND UPDATED_DTE <= ''2026-07-22 13:30:00'';
    ';  
    
    PRINT @SQL2;
    EXEC sp_executesql @SQL2;
    
    FETCH NEXT FROM TableCursor2 INTO @FullTableName;
END

CLOSE TableCursor2;
DEALLOCATE TableCursor2;

IF OBJECT_ID('tempdb..#TablesWithData') IS NOT NULL
    DROP TABLE #TablesWithData;