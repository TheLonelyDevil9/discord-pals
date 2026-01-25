# Contributing to Discord Pals

Thank you for your interest in contributing to Discord Pals! This document provides guidelines and instructions for contributing.

## Getting Started

### Prerequisites

- Python 3.10 or higher (3.11/3.12 recommended)
- Git
- A Discord account for testing

### Development Setup

1. **Fork and clone the repository**

   ```bash
   git clone https://github.com/YOUR_USERNAME/discord-pals.git
   cd discord-pals
   ```

2. **Create a virtual environment**

   ```bash
   python -m venv venv

   # Windows
   venv\Scripts\activate

   # Mac/Linux
   source venv/bin/activate
   ```

3. **Install dependencies**

   ```bash
   pip install -r requirements.txt
   ```

4. **Set up your test bot**

   - Create a test bot in the [Discord Developer Portal](https://discord.com/developers/applications)
   - Copy `.env.example` to `.env` and add your tokens
   - Create a `providers.json` with your AI provider config

## Code Style

- Follow [PEP 8](https://pep8.org/) style guidelines
- Use meaningful variable and function names
- Add docstrings to new functions and classes
- Keep functions focused and reasonably sized

## Making Changes

### Before You Start

1. Check existing [issues](https://github.com/TheLonelyDevil9/discord-pals/issues) to see if your idea is already being discussed
2. For significant changes, open an issue first to discuss your approach

### Development Workflow

1. **Create a feature branch**

   ```bash
   git checkout -b feature/your-feature-name
   ```

2. **Make your changes**

   - Write clear, concise commit messages
   - Test your changes thoroughly
   - Update documentation if needed

3. **Test locally**

   - Run the bot and verify your changes work
   - Test edge cases
   - Ensure existing functionality still works

### Testing Multi-Bot Features

When testing features that involve multiple bots:

- The `coordinator.py` system manages concurrent AI requests
- Test @mention features with `allow_bot_mentions` enabled
- Be cautious with `allow_bot_to_bot_mentions` - can cause infinite loops
- Use `/stop` command to halt bot-to-bot conversations during testing

## Submitting Changes

### Pull Request Process

1. **Push your branch**

   ```bash
   git push origin feature/your-feature-name
   ```

2. **Create a Pull Request**

   - Use a clear, descriptive title
   - Describe what changes you made and why
   - Reference any related issues

3. **Respond to feedback**

   - Be open to suggestions
   - Make requested changes promptly

### Version Bumping

**Important:** After your PR is merged, the maintainer will bump the version. If you're a maintainer merging changes:

```bash
# Bump patch version (1.2.3 -> 1.2.4) - for bug fixes
python bump_version.py patch --tag

# Bump minor version (1.2.3 -> 1.3.0) - for new features
python bump_version.py minor --tag

# Bump major version (1.2.3 -> 2.0.0) - for breaking changes
python bump_version.py major --tag

# With a custom changelog message
python bump_version.py patch --tag --message "Fixed login bug"
```

This will:
- Update `version.py`
- Add an entry to `CHANGELOG.md` with recent commits
- Create a git tag (e.g., `v1.2.4`)

Don't forget to push the tag: `git push origin v1.2.4`

## Reporting Bugs

When reporting bugs, please include:

- Python version (`python --version`)
- Operating system
- Steps to reproduce the issue
- Expected vs actual behavior
- Relevant error messages or logs

## Feature Requests

Feature requests are welcome! Please:

- Check if the feature has already been requested
- Clearly describe the feature and its use case
- Explain why it would benefit other users

## Questions?

If you have questions about contributing, feel free to open an issue for discussion.

---

Thank you for helping make Discord Pals better!
