# Requirements Document

## Introduction

本文档定义了 PDF 解读系统数据库架构重构的需求。重构目标是支持用户个性化解读功能、PDF 源文件存储、书籍处理流程追踪（上传→翻译→重构→解读），以及解读内容与书籍/章节的关联。

## Glossary

- **System**: PDF 解读系统的后端服务
- **User**: 使用系统的注册用户
- **Book**: 上传或重构生成的书籍实体
- **Chapter**: 书籍中的章节单元
- **Interpretation**: 基于章节内容生成的个性化解读
- **Prompt**: 用于生成解读的提示词模板
- **Migration_Service**: 负责数据迁移的服务模块
- **File_Storage**: 负责 PDF 源文件存储的模块
- **Hash**: 文件的 MD5 哈希值，用于去重

## Requirements

### Requirement 1: 用户管理

**User Story:** As a user, I want to register an account and set my profession, reading goals, and focus areas, so that I can receive personalized book interpretations.

#### Acceptance Criteria

1. WHEN a user registers with username and password, THE System SHALL create a new user record with hashed password
2. WHEN a user updates their profile, THE System SHALL store profession, reading_goal, and focus_areas fields
3. THE System SHALL enforce unique constraints on username and email fields
4. IF a user attempts to register with an existing username, THEN THE System SHALL return an error message
5. WHEN a user is deleted, THE System SHALL set user_id to NULL in related interpretations (soft reference)

### Requirement 2: 书籍管理增强

**User Story:** As a system administrator, I want to track each book's processing status (parsing/translating/ready), so that I can understand the processing progress.

#### Acceptance Criteria

1. THE System SHALL store book status with values: 'parsing', 'translating', 'ready'
2. WHEN a book is uploaded, THE System SHALL set initial status to 'parsing'
3. WHEN parsing completes and translation begins, THE System SHALL update status to 'translating'
4. WHEN all chapters are translated, THE System SHALL update status to 'ready'
5. THE System SHALL store source_type field with values: 'upload' or 'restructured'
6. WHEN a book is created from restructuring, THE System SHALL store parent_book_id reference
7. THE System SHALL store language field with values: 'zh', 'en', or 'mixed'

### Requirement 3: PDF 源文件存储

**User Story:** As a system, I need to store PDF source files and deduplicate by hash, to avoid uploading duplicate files.

#### Acceptance Criteria

1. WHEN a PDF file is uploaded, THE File_Storage SHALL save it to the uploads/ directory
2. WHEN a PDF file is uploaded, THE System SHALL calculate and store its MD5 hash
3. IF a file with the same hash already exists, THEN THE System SHALL return the existing book_id instead of creating a duplicate
4. THE System SHALL store file_path for each uploaded book
5. WHEN a book is deleted, THE System SHALL delete the associated PDF file from storage

### Requirement 4: 章节表重构

**User Story:** As a developer, I want to separate chapter metadata from content, so that queries on chapter lists are more efficient.

#### Acceptance Criteria

1. THE System SHALL store chapter metadata (title, title_zh, summary, word_count, is_translated) in the chapters table
2. THE System SHALL store chapter content (content, content_zh) in a separate chapter_contents table
3. WHEN a chapter is created, THE System SHALL assign a sequential chapter_index
4. THE System SHALL track translation status with is_translated flag (0=untranslated, 1=translated)
5. WHEN a chapter is translated, THE System SHALL update translated_at timestamp
6. WHEN a chapter is deleted, THE System SHALL cascade delete its content record

### Requirement 5: 重构映射追踪

**User Story:** As a system, I need to track the relationship between restructured books and original books, so that users can view the original source of restructured chapters.

#### Acceptance Criteria

1. WHEN a book is restructured, THE System SHALL create chapter_mappings records
2. THE System SHALL store source_chapter_ids as a JSON array (e.g., "[5,6,7,8]")
3. WHEN querying a restructured chapter, THE System SHALL be able to retrieve its source chapters
4. WHEN the source book is deleted, THE System SHALL set source_book_id to NULL (preserve mapping history)
5. WHEN the restructured book is deleted, THE System SHALL cascade delete its mappings

### Requirement 6: 解读表重构

**User Story:** As a user, I want to view all interpretation versions of a book (general and my personalized version), so that I can compare interpretations from different perspectives.

#### Acceptance Criteria

1. THE System SHALL associate interpretations with book_id (required) and chapter_id (optional for whole-book interpretations)
2. THE System SHALL associate interpretations with user_id (optional, NULL for general interpretations)
3. THE System SHALL store interpretation_type with values: 'standard' or 'personalized'
4. THE System SHALL store the actual prompt_text used as a snapshot
5. THE System SHALL store thinking_process from the LLM response
6. THE System SHALL store model_used to track which model generated the interpretation
7. WHEN querying interpretations, THE System SHALL be able to filter by book_id, chapter_id, user_id, and interpretation_type

### Requirement 7: 解读内容分表

**User Story:** As a developer, I want to separate interpretation metadata from content, so that listing interpretations is more efficient.

#### Acceptance Criteria

1. THE System SHALL store interpretation content in a separate interpretation_contents table
2. WHEN an interpretation is created, THE System SHALL create a corresponding content record
3. WHEN an interpretation is deleted, THE System SHALL cascade delete its content record
4. THE System SHALL enforce one-to-one relationship between interpretation and interpretation_contents

### Requirement 8: 提示词版本管理

**User Story:** As a developer, I want prompt version management, so that I can track the effectiveness of different prompt versions.

#### Acceptance Criteria

1. THE System SHALL store prompts with name, type, version, and content fields
2. THE System SHALL support prompt types: 'interpretation', 'restructure', 'translation', 'summary'
3. THE System SHALL track active status with is_active flag
4. WHEN a new prompt version is created, THE System SHALL allow setting it as active
5. WHEN generating interpretations, THE System SHALL record prompt_version in the interpretation record

### Requirement 9: 数据迁移

**User Story:** As a developer, I want to migrate existing data to the new structure, so that no data is lost during the restructuring.

#### Acceptance Criteria

1. WHEN migration runs, THE Migration_Service SHALL preserve all existing books data
2. WHEN migration runs, THE Migration_Service SHALL split chapter_summaries into chapters and chapter_contents tables
3. WHEN migration runs, THE Migration_Service SHALL attempt to associate existing interpretations with book_id by matching chapter_title
4. IF chapter_title matching fails, THEN THE Migration_Service SHALL log the unmatched interpretation for manual review
5. THE Migration_Service SHALL create the uploads/ directory if it does not exist
6. THE Migration_Service SHALL be idempotent (safe to run multiple times)

### Requirement 10: 系统配置保留

**User Story:** As a system, I need to preserve the existing settings table, so that system configuration is not affected by the restructuring.

#### Acceptance Criteria

1. THE System SHALL preserve the existing settings table structure (key, value, updated_at)
2. THE System SHALL continue to use settings for API keys and system configuration
3. WHEN migration runs, THE Migration_Service SHALL not modify existing settings data
