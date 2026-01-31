# Implementation Plan: Database Architecture Restructure

## Overview

本实现计划将 PDF 解读系统的数据库从 4 表结构重构为 9 表结构，支持用户管理、书籍状态追踪、PDF 文件存储、章节内容分离、重构映射、解读关联和提示词版本管理。实现采用增量方式，确保每个步骤都可验证。

## Tasks

- [x] 1. 创建新表结构和数据库初始化
  - [x] 1.1 创建 users 表
    - 添加 CREATE TABLE IF NOT EXISTS users 语句到 init_db()
    - 包含 id, username, email, password_hash, profession, reading_goal, focus_areas, created_at, updated_at 字段
    - 添加 UNIQUE 约束到 username 和 email
    - _Requirements: 1.1, 1.3_
  
  - [x] 1.2 重构 books 表
    - 添加新字段: source_type, parent_book_id, language, status, file_path, file_hash
    - 使用 add_column_if_missing() 函数添加新列（保持向后兼容）
    - 设置默认值: source_type='upload', language='zh', status='parsing'
    - _Requirements: 2.1, 2.5, 2.7, 3.4_
  
  - [x] 1.3 创建 chapters 表和 chapter_contents 表
    - 创建 chapters 表包含元数据字段
    - 创建 chapter_contents 表包含内容字段
    - 添加外键约束和级联删除
    - _Requirements: 4.1, 4.2, 4.6_
  
  - [x] 1.4 创建 chapter_mappings 表
    - 包含 new_book_id, new_chapter_id, source_book_id, source_chapter_ids 字段
    - 添加外键约束（source_book_id 使用 ON DELETE SET NULL）
    - _Requirements: 5.1, 5.4, 5.5_
  
  - [x] 1.5 重构 interpretations 表和创建 interpretation_contents 表
    - 添加新字段: book_id, chapter_id, user_id, interpretation_type, prompt_version, prompt_text, thinking_process, word_count, model_used
    - 创建 interpretation_contents 表
    - 添加外键约束
    - _Requirements: 6.1, 6.2, 6.3, 7.1_
  
  - [x] 1.6 创建 prompts 表
    - 包含 name, type, version, content, is_active, created_at 字段
    - _Requirements: 8.1, 8.2, 8.3_

- [x] 2. 实现 FileStorageService
  - [x] 2.1 实现文件存储和哈希计算
    - 创建 FileStorageService 类
    - 实现 save_file() 方法保存文件到 uploads/ 目录
    - 实现 calculate_hash() 方法计算 MD5 哈希
    - 实现 delete_file() 方法删除文件
    - _Requirements: 3.1, 3.2, 3.5_
  
  - [x] 2.2 编写 FileStorageService 属性测试
    - **Property 9: File Storage and Hash Calculation**
    - **Validates: Requirements 3.1, 3.2, 3.4**

- [x] 3. 实现 UserService
  - [x] 3.1 实现用户 CRUD 操作
    - 创建 create_user() 函数，使用 werkzeug.security 进行密码哈希
    - 创建 authenticate() 函数验证用户凭据
    - 创建 update_profile() 函数更新用户配置
    - 创建 get_user() 和 delete_user() 函数
    - _Requirements: 1.1, 1.2, 1.5_
  
  - [x] 3.2 编写 UserService 属性测试
    - **Property 1: User Password Hashing**
    - **Property 2: Profile Update Persistence**
    - **Property 3: Unique Constraint Enforcement**
    - **Property 4: User Deletion Soft Reference**
    - **Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.5**

- [x] 4. Checkpoint - 确保基础服务测试通过
  - 运行所有测试，确保 FileStorageService 和 UserService 正常工作
  - 如有问题请询问用户

- [x] 5. 实现 BookService
  - [x] 5.1 实现书籍 CRUD 和状态管理
    - 重构 store_book() 函数支持新字段
    - 创建 update_status() 函数管理状态转换
    - 创建 find_by_hash() 函数支持去重
    - 更新 list_books() 和 get_book_details() 支持新字段
    - 更新 delete_book() 级联删除文件
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.6, 3.3, 3.5_
  
  - [x] 5.2 编写 BookService 属性测试
    - **Property 5: Enum Field Validation**
    - **Property 6: Book Initial Status**
    - **Property 10: Hash-Based Deduplication**
    - **Property 11: File Deletion Cascade**
    - **Validates: Requirements 2.1, 2.2, 2.5, 2.7, 3.3, 3.5**

- [x] 6. 实现 ChapterService
  - [x] 6.1 实现章节 CRUD 操作
    - 创建 create_chapter() 函数，同时创建 chapter_contents 记录
    - 创建 update_translation() 函数更新翻译内容和状态
    - 创建 get_chapter() 函数支持可选包含内容
    - 创建 list_chapters() 函数列出章节元数据
    - 创建 delete_chapter() 函数（级联删除内容）
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6_
  
  - [x] 6.2 实现重构映射功能
    - 创建 create_mapping() 函数
    - 创建 get_source_chapters() 函数查询源章节
    - _Requirements: 5.1, 5.2, 5.3_
  
  - [x] 6.3 编写 ChapterService 属性测试
    - **Property 12: Chapter Data Separation**
    - **Property 13: Chapter Index Sequencing**
    - **Property 14: Translation Status Tracking**
    - **Property 15: Chapter Cascade Deletion**
    - **Property 16: Restructure Mapping JSON Round-Trip**
    - **Validates: Requirements 4.1-4.6, 5.1-5.3**

- [x] 7. Checkpoint - 确保核心服务测试通过
  - 运行所有测试，确保 BookService 和 ChapterService 正常工作
  - 如有问题请询问用户

- [x] 8. 实现 InterpretationService
  - [x] 8.1 实现解读 CRUD 操作
    - 重构 store_interpretation() 函数支持新字段和分表存储
    - 创建 get_interpretation() 函数支持可选包含内容
    - 创建 list_interpretations() 函数支持多条件筛选
    - 创建 delete_interpretation() 函数（级联删除内容）
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 7.1, 7.2, 7.3, 7.4_
  
  - [x] 8.2 编写 InterpretationService 属性测试
    - **Property 19: Interpretation Data Separation**
    - **Property 20: Interpretation Required and Optional Fields**
    - **Property 21: Interpretation Filtering**
    - **Property 22: Interpretation Cascade Deletion**
    - **Validates: Requirements 6.1-6.7, 7.1-7.4**

- [x] 9. 实现 PromptService
  - [x] 9.1 实现提示词版本管理
    - 创建 create_prompt() 函数
    - 创建 get_active_prompt() 函数获取激活版本
    - 创建 set_active() 函数设置激活状态（同类型互斥）
    - 创建 list_prompts() 函数
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5_
  
  - [x] 9.2 编写 PromptService 属性测试
    - **Property 23: Prompt Activation Exclusivity**
    - **Validates: Requirements 8.3, 8.4**

- [x] 10. Checkpoint - 确保所有服务测试通过
  - 运行所有测试，确保 InterpretationService 和 PromptService 正常工作
  - 如有问题请询问用户

- [x] 11. 实现 MigrationService
  - [x] 11.1 实现数据迁移逻辑
    - 创建 MigrationService 类
    - 实现 migrate_books() 迁移书籍数据（添加默认值）
    - 实现 migrate_chapters() 拆分 chapter_summaries 到新表
    - 实现 migrate_interpretations() 关联解读到书籍
    - 实现 create_upload_directory() 创建上传目录
    - 实现 run_migration() 主入口（幂等）
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6_
  
  - [x] 11.2 编写 MigrationService 属性测试
    - **Property 24: Migration Data Preservation**
    - **Property 25: Migration Idempotency**
    - **Property 26: Settings Functionality Preservation**
    - **Validates: Requirements 9.1-9.6, 10.2, 10.3**

- [ ] 12. 更新 API 路由
  - [x] 12.1 添加用户管理 API
    - POST /api/users/register - 用户注册
    - POST /api/users/login - 用户登录
    - GET /api/users/<user_id> - 获取用户信息
    - PUT /api/users/<user_id>/profile - 更新用户配置
    - DELETE /api/users/<user_id> - 删除用户
    - _Requirements: 1.1, 1.2, 1.5_
  
  - [x] 12.2 更新书籍管理 API
    - 更新 POST /api/parse/pdf 支持文件存储和去重
    - 添加 GET /api/admin/books/<book_id>/status - 获取书籍状态
    - 添加 PUT /api/admin/books/<book_id>/status - 更新书籍状态
    - 更新 DELETE /api/admin/books/<book_id> 级联删除文件
    - _Requirements: 2.1-2.7, 3.1-3.5_
  
  - [x] 12.3 更新章节管理 API
    - 更新现有章节 API 使用新表结构
    - 添加 GET /api/admin/chapters/<chapter_id>/source - 获取源章节映射
    - _Requirements: 4.1-4.6, 5.1-5.5_
  
  - [x] 12.4 更新解读管理 API
    - 更新 POST /api/generate/interpretation 支持新字段
    - 添加 GET /api/interpretations - 支持多条件筛选
    - 更新响应格式包含新字段
    - _Requirements: 6.1-6.7, 7.1-7.4_
  
  - [x] 12.5 添加提示词管理 API
    - POST /api/prompts - 创建提示词版本
    - GET /api/prompts - 列出提示词
    - GET /api/prompts/active/<type> - 获取激活提示词
    - PUT /api/prompts/<prompt_id>/activate - 设置激活状态
    - _Requirements: 8.1-8.5_
  
  - [x] 12.6 添加迁移 API
    - POST /api/admin/migrate - 执行数据迁移
    - GET /api/admin/migrate/status - 获取迁移状态
    - _Requirements: 9.1-9.6_

- [x] 13. Checkpoint - 确保所有 API 测试通过
  - 运行所有测试，确保 API 路由正常工作
  - 如有问题请询问用户

- [x] 14. 集成测试和最终验证
  - [x] 14.1 编写端到端集成测试
    - 测试完整的书籍上传→解析→翻译→解读流程
    - 测试用户注册→配置→个性化解读流程
    - 测试书籍重构和映射追踪
    - _Requirements: All_
  
  - [x] 14.2 编写迁移集成测试
    - 使用现有数据库测试迁移
    - 验证数据完整性
    - _Requirements: 9.1-9.6, 10.1-10.3_

- [x] 15. Final Checkpoint - 确保所有测试通过
  - 运行完整测试套件
  - 验证所有需求已实现
  - 如有问题请询问用户

## Notes

- All tasks are required for comprehensive implementation
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties
- Unit tests validate specific examples and edge cases
- 迁移服务设计为幂等，可安全多次运行
- 所有新字段使用默认值保持向后兼容
