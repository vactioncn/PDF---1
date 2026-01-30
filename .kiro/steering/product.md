# Product Overview

PDF解读系统 (PDF Interpretation System) - A Chinese-language web application for intelligent book/document analysis and personalized content generation.

## Core Purpose
Transform PDF and EPUB documents into personalized, AI-generated interpretations tailored to user profiles (profession, reading goals, focus areas).

## Key Features
- **Document Parsing**: Extract and clean table of contents from PDF/EPUB files using LLM-powered TOC cleaning
- **Personalized Interpretation**: Generate customized chapter interpretations with:
  - Personalized intro summaries
  - Main content explanations (adjustable density: 20%/50%/70%)
  - Real-world application examples
  - Quiz questions with explanations
  - Thought-provoking questions
- **Deep Thinking Integration**: Uses Doubao (豆包) deep thinking model for reasoning-enhanced content generation
- **Book Management**: Store, manage, and organize parsed books and chapters
- **Article Restructuring**: Split long articles into semantically coherent segments with auto-generated titles

## Target Users
Chinese-speaking readers who want AI-assisted book comprehension and personalized learning experiences.

## External Services
- **Doubao (豆包) API**: Primary LLM for content generation and deep thinking (volcenginesdkarkruntime SDK)
- **DeepSeek API**: Alternative LLM provider
- **Volcengine TTS**: Text-to-speech for podcast generation (experimental)
