-- Add photo_url column to users table for profile pictures

-- Check if column exists before adding
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'users' AND column_name = 'photo_url'
    ) THEN
        ALTER TABLE users ADD COLUMN photo_url TEXT;
        RAISE NOTICE 'Added photo_url column to users table';
    ELSE
        RAISE NOTICE 'photo_url column already exists';
    END IF;
END $$;

-- Also add phone column if it doesn't exist (for profile settings)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'users' AND column_name = 'phone'
    ) THEN
        ALTER TABLE users ADD COLUMN phone VARCHAR(50);
        RAISE NOTICE 'Added phone column to users table';
    ELSE
        RAISE NOTICE 'phone column already exists';
    END IF;
END $$;
