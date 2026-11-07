# 线上课程目录

这里放正式发布的课程包，每门课一个目录，结构和规则见 `../CONTRACT.md`。

- 新增或修改课程后运行 `python -m deeptutor.learning.courses validate`；
- 版本发布（course.json 里改成 published）后运行 `python -m deeptutor.learning.courses lock`，
  把内容哈希写进 `courses.lock.json` 一起提交。之后这个版本目录就不能再改了。
- 开发联调用模拟课程：`DEEPTUTOR_COURSES_DIR=tests/fixtures/courses/catalog`。
