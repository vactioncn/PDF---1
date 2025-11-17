# API接口和函数清单

## 📋 API接口列表

### 一、PDF/EPUB解析相关接口

**1. POST /api/parse/clean_toc** ✅ **已修复**
- 功能：清洗目录接口（支持PDF和EPUB）
- 输入：文件上传
- 输出：清洗后的目录列表
- **问题历史**：
  - ❌ **EPUB解析问题**：之前直接使用`BytesIO`传给`epub.read_epub()`会报错：`stat: path should be string, bytes, os.PathLike or integer, not BytesIO`
  - ✅ **修复方案**：改用临时文件，先写入临时文件，再传给`epub.read_epub()`，使用完后删除临时文件（见代码2574-2585行）
- **解决的问题**：将PDF/EPUB的原始目录通过LLM清洗，去除版权页、序言等非正文章节，生成干净的目录结构

**2. POST /api/parse/debug_toc** ✅ **正常**
- 功能：调试接口，仅提取原始目录，不进行LLM清洗（支持PDF和EPUB）
- 输入：文件上传
- 输出：原始目录列表
- **解决的问题**：用于调试，查看PDF/EPUB的原始目录结构，不经过LLM处理，便于排查问题

**3. POST /api/parse/extract** ✅ **已修复**
- 功能：提取内容接口（支持PDF和EPUB）
- 输入：cleaned_toc（清洗后的目录）、文件上传
- 输出：章节内容列表
- **问题历史**：
  - ❌ **EPUB内容提取问题**：同样存在BytesIO问题
  - ✅ **修复方案**：使用临时文件方式（见代码3093-3100行）
- **解决的问题**：根据清洗后的目录，从PDF/EPUB文件中提取每个章节的正文内容，并统计字数

**4. POST /api/parse** ⚠️ **旧接口，建议不使用**
- 功能：解析PDF文档（旧接口，仅支持PDF）
- 输入：文件上传
- 输出：解析结果
- **问题**：仅支持PDF，不支持EPUB，功能已被接口1-3替代
- **建议**：使用接口1-3的组合流程（clean_toc → extract）

**5. POST /api/parse/ingest** ✅ **正常**
- 功能：将解析结果批量入库
- 输入：entries（章节列表）
- 输出：入库结果
- **解决的问题**：将解析后的所有章节一次性保存到数据库，用于后续的书籍管理和内容生成

**6. POST /api/parse/ingest/start** ✅ **正常**
- 功能：开始入库流程（分步入库的第一步）
- 输入：filename, chapter_count
- 输出：book_id
- **解决的问题**：分步入库流程的开始，创建书籍记录，返回book_id供后续章节入库使用

**7. POST /api/parse/ingest/chapter** ✅ **正常**
- 功能：逐个章节入库（分步入库的第二步）
- 输入：entry（章节对象）
- 输出：入库结果
- **解决的问题**：分步入库流程，逐个章节保存，适合大量章节的场景，避免一次性提交导致超时

---

### 二、提示词相关接口 ⭐ **核心配置接口**

**8. GET /api/settings/prompt_parts** ✅ **正常**
- 功能：获取分解后的提示词各部分
- 输出：intro_prompt, body_prompt, quiz_prompt, question_prompt
- **解决的问题**：将完整的提示词拆分为4个部分（导读、正文、选择题、思考题），便于分别调优和管理
- **详细说明**：
  - `intro_prompt`：个性化导读摘要（150-300字）
  - `body_prompt`：正文讲解（根据密度20%/50%/70%）
  - `quiz_prompt`：知识点选择题（3-5题）
  - `question_prompt`：一个强有力的思考问题

**9. POST /api/settings/prompt_parts** ✅ **正常**
- 功能：保存分解后的提示词各部分
- 输入：intro_prompt, body_prompt, quiz_prompt, question_prompt
- 输出：status
- **解决的问题**：保存修改后的提示词各部分，持久化到数据库，供后续生成使用

**10. GET /api/settings/master_prompt** ⚠️ **兼容旧接口**
- 功能：获取完整的主提示词（兼容旧接口）
- 输出：完整提示词
- **问题**：这是旧接口，如果已设置分解后的提示词，会自动组合；否则返回默认完整提示词
- **建议**：优先使用接口8获取分解后的提示词，更灵活

**11. POST /api/settings/master_prompt** ⚠️ **兼容旧接口**
- 功能：保存完整的主提示词
- 输入：value（提示词内容）
- 输出：status
- **问题**：这是旧接口，保存的是完整提示词，不会自动拆分
- **建议**：优先使用接口9保存分解后的提示词，便于管理

---

### 三、内容生成相关接口

**12. POST /api/generate** ✅ **已优化**
- 功能：生成章节解读内容（完整版，包含5个部分）
- 输入：chapterTitle, chapterText, userProfession, readingGoal, focus, density, intro_prompt（可选）, body_prompt（可选）, quiz_prompt（可选）, question_prompt（可选）
- 输出：result（解读结果，包含reasoning_content和content）, record_id
- **问题历史**：
  - ❌ **JSON解析失败**：LLM返回的JSON中包含控制字符（ASCII 0-31），导致解析失败
  - ✅ **修复方案**：实现了`_fix_json_string()`和`_fix_json_control_chars()`函数，自动修复控制字符（见代码1107-1700行）
  - ❌ **思考过程提取问题**：需要从LLM响应中提取`reasoning_content`（思考过程）和`content`（最终结果）
  - ✅ **修复方案**：实现了`_add_debug_info_to_result()`函数，自动提取和分离思考过程和最终结果
- **解决的问题**：根据用户画像（职业、阅读目标、关注点、密度）和章节内容，生成个性化的解读，包含导读、正文讲解、应用场景、选择题、思考题

**13. POST /api/generate/part** ✅ **正常**
- 功能：单独生成某个部分的解读（用于调试和调优）
- 输入：part（intro/body/quiz/question）, chapterTitle, userProfession, readingGoal, focus, density, chapterText或chapterSummary
- 输出：result（部分解读结果）
- **解决的问题**：用于调试和调优，单独测试某个部分的生成效果，不需要生成完整内容，节省时间和成本

---

### 四、翻译相关接口

**14. POST /api/translate**
- 功能：翻译API端点，用于测试翻译功能
- 输入：text
- 输出：translated（翻译结果）

**15. POST /api/translate/chapter**
- 功能：翻译单个章节的标题和内容，并生成中文概要
- 输入：title, content
- 输出：title_zh, content_zh, summary

---

### 五、播客相关接口

**16. POST /api/podcast/generate** ❌ **有问题，不建议使用**
- 功能：生成播客音频
- 输入：text, voice_type
- 输出：音频数据
- **问题历史**：
  - ❌ **错误的API端点**：使用了`wss://openspeech.bytedance.com/api/v3/tts/bidirection`（通用TTS端点）
  - ✅ **正确端点**：应该是`wss://openspeech.bytedance.com/api/v3/sami/podcasttts`（播客专用端点）
  - ❌ **错误的协议**：使用了简单的JSON WebSocket消息
  - ✅ **正确协议**：需要使用自定义二进制协议（protocols模块）
  - ❌ **缺少必要的请求头**：缺少`X-Api-App-Key: "aGjiRDfUWi"`（固定值）
  - ❌ **错误的WebSocket库**：使用了`websocket-client`（同步库）
  - ✅ **正确库**：应该使用`websockets`（异步库）
- **当前状态**：代码中已导入protocols模块和websockets，但可能仍有问题，建议测试后再使用
- **参考文档**：见`PODCAST_ISSUE_SUMMARY.md`

---

### 六、文章重构相关接口

**17. POST /api/restructure/split-article** ⭐ ✅ **标准接口，推荐使用**
- 功能：标准文章分割接口
- 输入：text（文本内容，必需）, max_length（每段最大字数，必需）, title（可选，默认"未命名文章"）
- 输出：segments（分割后的段落列表，每个包含title, content, word_count）
- **解决的问题**：
  1. **长文章分割**：将超过字数限制的长文章分割成多个段落
  2. **语义完整性**：使用TextTiling风格的语义分割，保持话题完整性
  3. **严格字数控制**：确保每个段落不超过指定的字数限制
  4. **智能标题生成**：为每个分割后的段落自动生成语义化的标题（使用flash模型，速度快）
- **工作流程**：
  1. 语义分割：使用`semantic_segmentation()`识别话题边界
  2. 字数打包：使用`pack_segments_by_word_count()`按字数限制重新打包
  3. 标题生成：为每个打包块调用LLM生成标题
- **使用场景**：书籍章节重构、长文章分段、内容重组

**18. POST /api/restructure/split-chapter** ⚠️ **兼容旧接口，建议使用17**
- 功能：分割超过1万字的章节（兼容旧接口）
- 输入：title（必需）, content（必需）, max_word_count（默认10000）, word_count（可选）
- 输出：segments
- **问题**：这是旧接口，参数命名不一致（使用`content`而不是`text`，使用`max_word_count`而不是`max_length`）
- **建议**：使用接口17（`split-article`），参数更清晰，接口更标准化

---

### 七、管理相关接口

**19. GET /api/admin/books**
- 功能：查询所有已入库的书籍
- 输入：page, per_page（可选）
- 输出：books列表

**20. GET /api/admin/books/<int:book_id>**
- 功能：获取书籍详情（包括章节列表）
- 输入：book_id（URL参数）
- 输出：book详情和chapters列表

**21. DELETE /api/admin/books/<int:book_id>**
- 功能：删除指定书籍（会级联删除所有章节）
- 输入：book_id（URL参数）
- 输出：删除结果

**22. POST /api/admin/books/batch_delete**
- 功能：批量删除书籍
- 输入：ids（书籍ID列表）
- 输出：删除结果

---

### 八、测试相关接口

**23. POST /api/test/doubao-thinking** ✅ **正常**
- 功能：纯测试接口，直接调用豆包深度思考模型，无额外逻辑
- 输入：prompt, temperature（可选，默认0.3）, max_tokens（可选，默认16000）
- 输出：reasoning_content（思考过程）, content（最终结果）
- **解决的问题**：用于测试豆包深度思考模型的能力，验证模型是否能正确返回思考过程和最终结果，不涉及业务逻辑

---

## 🔧 核心函数列表

### 一、文章分割相关函数

**F1. split_article_into_segments()** ⭐ ✅ **核心函数，已优化**
- 功能：将文章分割成多个段落，每个段落不超过指定字数，并为每个段落生成标题
- 输入：title, content, max_word_count（默认10000）, api_key（可选，从环境变量获取）, similarity_threshold（默认0.3）
- 输出：List[Dict] - 每个字典包含title（生成的标题）, content（段落内容）, word_count（字数）
- **问题历史**：
  - ❌ **初始实现问题**：直接让LLM按字数分割，LLM无法精确控制字数，导致某些段落超过限制
  - ✅ **修复方案**：改为三步流程：语义分割 → 字数打包 → 标题生成，确保严格字数控制
  - ✅ **模型优化**：标题生成使用`doubao-seed-1-6-flash-250828`（flash模型），速度快
- **解决的问题**：将长文章智能分割，保持语义完整性，严格字数控制，自动生成标题

**F2. semantic_segmentation()** ✅ **正常**
- 功能：使用TextTiling风格的语义分割，将文本分割成语义片段
- 输入：text, similarity_threshold（默认0.3）
- 输出：List[str] - 语义片段列表
- **算法原理**：
  1. 将文本分割成句子
  2. 计算相邻句子的相似度（Jaccard相似度）
  3. 使用滑动窗口找到相似度低谷（分割点）
  4. 在分割点处切分文本
- **解决的问题**：识别文本中的话题边界，将文本分割成语义完整的片段

**F3. pack_segments_by_word_count()** ✅ **正常**
- 功能：在语义片段基础上，按字数限制重新打包
- 输入：segments（语义片段列表）, max_word_count（最大字数）
- 输出：List[str] - 打包后的文本块列表
- **算法逻辑**：
  1. 遍历语义片段，累加字数
  2. 如果累加后超过限制，保存当前chunk，开始新的chunk
  3. 如果单个片段超过限制，进一步按段落或句子分割
- **解决的问题**：在保持语义完整性的前提下，确保每个文本块不超过字数限制

**F4. calculate_word_count()** ✅ **正常**
- 功能：计算字数（去除所有空格）
- 输入：text
- 输出：int - 字数
- **解决的问题**：准确计算文本字数（去除空格），用于字数限制和统计

**F5. split_into_sentences()** ✅ **正常**
- 功能：将文本分割成句子
- 输入：text
- 输出：List[str] - 句子列表
- **分割规则**：使用正则表达式识别句号、问号、感叹号、分号等
- **解决的问题**：将文本分割成句子，用于语义分割和相似度计算

**F6. calculate_sentence_similarity()** ✅ **正常**
- 功能：计算两个句子的相似度（使用词重叠方法，Jaccard相似度）
- 输入：sent1, sent2
- 输出：float - 相似度（0-1）
- **算法**：Jaccard相似度 = 交集大小 / 并集大小
- **解决的问题**：计算句子之间的相似度，用于识别话题边界

---

### 二、提示词构建相关函数

**F7. build_generation_prompt()** ✅ **正常**
- 功能：根据用户提供的提示词部分构建完整的生成提示词
- 输入：prompt_parts（字典，包含intro_prompt, body_prompt, quiz_prompt, question_prompt）, payload（用户输入，包含章节内容、用户画像等）
- 输出：str - 完整的提示词
- **处理逻辑**：
  1. 替换占位符：{chapter_fulltext}, {chapter_summary}, {user_profession}, {reading_goal}, {focus_preference}, {explanation_density}
  2. 组合各部分提示词
  3. 添加输入数据JSON
- **解决的问题**：将分解的提示词部分组合成完整提示词，替换占位符，供LLM使用

**F8. get_master_prompt()** ⚠️ **兼容旧接口**
- 功能：获取完整的主提示词（兼容旧接口）
- 输出：完整提示词JSON
- **问题**：如果已设置分解后的提示词，会自动组合；否则返回默认完整提示词
- **建议**：优先使用接口8获取分解后的提示词

---

### 三、LLM调用相关函数

**F9. call_llm()** ✅ **已优化**
- 功能：使用豆包深度思考模型进行解读生成
- 输入：prompt_parts（提示词部分）, payload（用户输入）
- 输出：Dict - 包含reasoning_content（思考过程）和content（最终结果）
- **问题历史**：
  - ❌ **JSON解析失败**：LLM返回的JSON中包含控制字符，导致解析失败
  - ✅ **修复方案**：调用`_fix_json_string()`自动修复控制字符
  - ❌ **思考过程提取问题**：需要从响应中提取`reasoning_content`和`content`
  - ✅ **修复方案**：调用`_add_debug_info_to_result()`自动提取
  - ✅ **降级机制**：优先使用SDK（volcenginesdkarkruntime），失败则降级使用requests
- **解决的问题**：调用豆包深度思考模型，生成章节解读，自动处理JSON解析和思考过程提取

---

### 四、数据库相关函数

**F10. store_interpretation()**
- 功能：存储解读结果到数据库
- 输入：payload, master_prompt, result
- 输出：record_id

**F11. load_setting() / store_setting()**
- 功能：加载/保存设置（如提示词配置）
- 输入：key, default（可选）
- 输出：设置值

---

## 📝 页面路由

**P1. GET /** 或 **GET /index.html** - 首页

**P2. GET /parser_test_page.html** - PDF解析测试页面

**P3. GET /aigen_test_page.html** - AI生成测试页面

**P4. GET /book_restructure_page.html** - 书籍重构页面

**P5. GET /admin.html** - 管理页面

**P6. GET /admin_books.html** - 书籍管理页面

**P7. GET /test_thinking.html** - 深度思考模型测试页面

**P8. GET /test_doubao_thinking.html** - 豆包深度思考能力纯测试页面

---

## 🎯 接口状态标记说明

- ✅ **正常/已修复**：接口工作正常，或已修复历史问题
- ⚠️ **兼容旧接口**：旧接口，建议使用新接口
- ❌ **有问题**：接口存在问题，不建议使用
- ⭐ **特别重要**：核心功能接口

## 🔍 主要问题总结

### 已修复的问题

1. **EPUB解析BytesIO问题**（接口1、3）
   - 问题：`epub.read_epub()`不接受BytesIO，需要文件路径
   - 修复：使用临时文件，写入后传给函数，使用完后删除

2. **JSON控制字符问题**（接口12、函数F9）
   - 问题：LLM返回的JSON包含控制字符（ASCII 0-31），导致解析失败
   - 修复：实现了`_fix_json_string()`函数，自动转义控制字符

3. **文章分割字数控制问题**（接口17、函数F1）
   - 问题：直接让LLM按字数分割，无法精确控制
   - 修复：改为三步流程：语义分割 → 字数打包 → 标题生成

### 已知问题

1. **播客API问题**（接口16）
   - 使用了错误的端点和协议
   - 需要改用正确的播客专用端点和二进制协议
   - 参考：`PODCAST_ISSUE_SUMMARY.md`

2. **TTS V1 API问题**
   - V1 API可能不支持，建议使用V3 WebSocket接口
   - 参考：`volcengine_tts_v1_issue_summary.txt`

---

## 📌 使用建议

### 推荐流程

1. **提示词配置**：
   - ✅ 使用接口8获取分解后的提示词
   - ✅ 使用接口9保存修改后的提示词
   - ⚠️ 避免使用接口10-11（旧接口）

2. **文档解析**：
   - ✅ 使用接口1清洗目录
   - ✅ 使用接口3提取内容
   - ✅ 使用接口5批量入库（或接口6-7分步入库）
   - ⚠️ 避免使用接口4（旧接口，仅支持PDF）

3. **内容生成**：
   - ✅ 使用接口12生成完整解读
   - ✅ 使用接口13调试单个部分

4. **文章重构**：
   - ✅ 使用接口17（split-article）进行文章分割
   - ⚠️ 避免使用接口18（旧接口，参数不一致）

5. **管理操作**：
   - ✅ 使用接口19-22进行书籍管理

6. **测试**：
   - ✅ 使用接口23测试豆包深度思考模型
   - ❌ 避免使用接口16（播客API有问题）

### 最佳实践

- **新项目**：优先使用标记为✅的接口
- **旧项目迁移**：逐步从旧接口迁移到新接口
- **问题排查**：遇到问题先查看"问题历史"部分

