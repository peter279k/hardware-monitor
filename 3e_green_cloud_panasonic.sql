CREATE TABLE IF NOT EXISTS "table_name" (
    current float not null,
    temperature float not null,
    watts float not null,
    measured_datetime TIMESTAMP unique
)
