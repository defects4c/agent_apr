#!/usr/bin/env bash
# swe_agent/tasks/utils/defects4j.sh
# Usage: defects4j.sh <function> <project> <bug_id> <work_dir> <java_home> <d4j_path> [extra1] [extra2]

FUNCTION=$1
PROJECT=$2
BUG_ID=$3
WORK_DIR=$4
export JAVA_HOME=$5
D4J_PATH=$6
EXTRA1=$7
EXTRA2=$8

export PATH="$D4J_PATH/framework/bin:$JAVA_HOME/bin:$PATH"

checkout_bug()    { defects4j checkout -p "$PROJECT" -v "${BUG_ID}b" -w "$WORK_DIR"; }
compile_bug()     { cd "$WORK_DIR" && defects4j compile; }
test_bug()        { cd "$WORK_DIR" && defects4j test; }
validate_patch()  {
    cd "$WORK_DIR"
    echo "$EXTRA1" | git apply --whitespace=fix -
    defects4j compile && defects4j test
}
get_patch_git_diff() { cd "$WORK_DIR" && git diff; }
get_test_error()     { cd "$WORK_DIR" && defects4j export -p tests.trigger; }

$FUNCTION
