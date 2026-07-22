from pydantic import BaseModel, ValidationError, Field
from enum import Enum


# class Student(BaseModel):
#     name: str          # 字符串
#     age: int           # 整数
#
# # 用关键字参数创建实例
# s = Student(name="小明", age=18)
# print(s.name)          # 小明
# print(s.age)           # 18
# print(s)               # name='小明' age=18

#####################################################################################


# class Student:
#     def __init__(self, name: str, age: int):
#         self.name = name
#         self.age = age
#
# s = Student("小明", 18)
# print(s.name)
# print(s.age)
# print(s)

#####################################################################################


# class Student(BaseModel):
#     name: str
#     age: int
#
# try:
#     Student(name="小刚", age="十八")   # "十八" 无法转成整数
# except ValidationError as e:
#     print(e)

# ==============================================================================


# class UserProfile(BaseModel):
#     name: str = Field(description="用户姓名")  # 必填：没有默认值
#     phone: str = Field(default="", description="手机号")  # 可选：有默认值 ""
#     age: int = Field(default=18, description="年龄")  # 可选：默认 18
#     tags: list[str] = Field(default_factory=list, description="标签列表")
#
#
# a = UserProfile(name="Python 入门")
# print(a.name)
# print(a.phone)
# print(a.age)
# print(a.tags)


# ==============================================================================

#
# class EducationItem(BaseModel):
#     school: str = Field(description="学校名称")
#     major: str = Field(description="专业名称")
#     nikename: str = None
#
#
# class Resume(BaseModel):
#     name: str = Field(description="姓名")
#     education: list[EducationItem] = Field(default_factory=list)  # 教育经历列表
#
#
# # 创建时，嵌套部分可以直接用字典，Pydantic 会自动转成对应的子模型
# resume = Resume(
#     name="小明",
#     education=[
#         {"school": "清华大学", "major": "计算机"},
#         {"school": "北京大学", "major": "软件工程"},
#     ],
# )
#
# resume1 = Resume(
#     name="小明",
#     education=[
#         EducationItem(school="清华大学", major="计算机"),
#         EducationItem(school="北京大学", major="软件工程"),
#     ]
# )
#
# print(resume.name)
# print(resume.education[0].school)
# print(resume.education[0].nikename)
# print(type(resume.education[0]))
#
# print(resume1.name)
# print(resume1.education[0].school)
# print(type(resume1.education[0]))


# ==============================================================================


# class Student(BaseModel):
#     name: str = Field(description="姓名")
#     age: int = Field(default=18, description="年龄")
#
#
# s = Student(name="小明", age=20)
# d = s.model_dump()  # 模型 → 字典
#
# print(d)  # {'name': '小明', 'age': 20}
# print(type(d))  # <class 'dict'>


# ==============================================================================


# class EducationItem(BaseModel):
#     school: str = Field(description="学校名称")
#     major: str = Field(description="专业名称")
#     nikename: str = None
#
#
# class Resume(BaseModel):
#     name: str = Field(description="姓名")
#     education: list[EducationItem] = Field(default_factory=list)  # 教育经历列表
#
#
# # 创建时，嵌套部分可以直接用字典，Pydantic 会自动转成对应的子模型
# resume = Resume(
#     name="小明",
#     education=[
#         {"school": "清华大学", "major": "计算机"},
#         {"school": "北京大学", "major": "软件工程"},
#     ],
# )
#
# x = resume.model_dump()
# print(x)


# ==============================================================================


class InterviewStage(str, Enum):  # 继承 str，取值就是字符串
    WARMUP = "warmup"
    TECH_BASE = "tech_base"
    PROJECT = "project"
    CLOSING = "closing"
    FINISHED = "finished"


print(InterviewStage.WARMUP)  # InterviewStage.WARMUP
print(InterviewStage.WARMUP.value)


# ==============================================================================











