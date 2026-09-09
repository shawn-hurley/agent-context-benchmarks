# ⚠️ CRITICAL: SKILL LOADING REQUIRED

## STEP 1: Load rgctl Skill (MANDATORY - DO THIS FIRST)

**YOU MUST load the rgctl skill as your ABSOLUTE FIRST ACTION before analyzing any code.**

Use the `load_skill` tool with argument `"rgctl"`:

```
load_skill "rgctl"
```

**DO NOT PROCEED to any code analysis until you confirm the skill is loaded.**

---

## STEP 2: Build Knowledge Graph (MANDATORY - DO THIS IMMEDIATELY AFTER)

**Immediately after loading the skill**, build the code knowledge graph for the repository:

```bash
shell rgctl discover /testbed
```

**Wait for this command to complete. DO NOT use any other code exploration tools until this finishes.**

---

## STEP 3: Use rgctl for ALL Code Analysis (ENFORCED)

### ❌ FORBIDDEN TOOLS - Do NOT Use These

**You are PROHIBITED from using these tools for code analysis after Step 2:**

- ❌ `tree` — FORBIDDEN after Step 2
- ❌ `analyze` — FORBIDDEN after Step 2  
- ❌ `grep` — FORBIDDEN after Step 2
- ❌ `find` — FORBIDDEN after Step 2
- ❌ `sed` to preview files — FORBIDDEN after Step 2

**If you use any of these AFTER loading rgctl and building the graph, you have FAILED the required workflow.**

### ✅ REQUIRED rgctl Commands Instead

**Use these rgctl commands INSTEAD of the forbidden tools:**

#### Find a Function or Class by Name
```bash
# Instead of: grep -r "prepare_content_length"
# Use: rgctl to find it
shell rgctl -f json gql "MATCH (n:Function) WHERE n.name LIKE '*prepare_content_length*' RETURN n"
```

#### Find Where a Function is Called
```bash
# Instead of: grep "function_name" and reading files
# Use: rgctl to find all callers
shell rgctl -f json gql "MATCH (a:Function)-[:CALLS]->(b:Function {name:'prepare_content_length'}) RETURN a, b LIMIT 20"
```

#### Understand a File's Structure
```bash
# Instead of: analyze or tree
# Use: rgctl to list functions in a file
shell rgctl -f json gql "MATCH (n:Function) WHERE n.file_path CONTAINS 'models.py' RETURN n.name, n.line_number"
```

#### Find Dependencies and Imports
```bash
# Instead of: reading files to see imports
# Use: rgctl dependencies
shell rgctl -f json dependencies "/testbed/requests/models.py"
```

#### Analyze Impact Before Making Changes
```bash
# Instead of: grepping to find usage
# Use: rgctl blast-radius to see impact
shell rgctl -f json blast-radius PreparedRequest --depth 3
```

#### List All Functions in Repository
```bash
# Instead of: tree or find
# Use: rgctl to inventory all functions
shell rgctl -f json gql --macro-name all_functions unused
```

---

## REQUIRED WORKFLOW - You MUST Follow This Exactly

**Every single time you analyze code, follow this sequence:**

1. ✅ Load skill: `load_skill "rgctl"`
2. ✅ Build graph: `shell rgctl discover /testbed`
3. ✅ Query rgctl for structural answers
4. ✅ Identify exact files/lines from rgctl results
5. ✅ ONLY NOW use `analyze` to read those specific files
6. ✅ Make your fix
7. ✅ Test your changes

**If you deviate from this workflow, you will fail.**

---

## Why This Workflow is Mandatory

**Efficiency**: One `rgctl discover` builds the entire code graph. Each query returns ONLY what you need.

**Correctness**: rgctl understands code structure (functions, calls, dependencies). `grep` only matches text.

**Speed**: Graph queries are instant. No need to read multiple files repeatedly.

**Context**: Shows relationships and impact, not just string matches.

---

## Repository Guidelines

- Working directory: `/testbed`
- Do NOT modify test files
- Run tests after changes to confirm the fix works
- Do NOT rely on memorized version history - only use the code in front of you

---

## Issue-Solving Strategy Using rgctl

When you encounter a GitHub issue:

1. **Read the issue** to understand the problem
2. **Load rgctl**: `load_skill "rgctl"` 
3. **Build graph**: `shell rgctl discover /testbed`
4. **Use rgctl queries** to find relevant code sections
5. **Query for callers/impact**: Use blast-radius to understand scope
6. **Identify the fix location** from rgctl results
7. **Read only those files** you identified
8. **Make minimal changes**
9. **Test thoroughly**

**Remember**: rgctl is your code search and navigation tool. Use it FIRST, before reading any files.
