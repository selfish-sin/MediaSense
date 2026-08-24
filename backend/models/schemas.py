"""
YouTube Tool V2 - Pydantic 数据模型
所有API的输入输出自动校验
"""
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from enum import Enum


# ============ 任务状态枚举 ============
class JobStatus(str, Enum):
    pending = "pending"
    downloading = "downloading"
    transcribing = "transcribing"
    preview = "preview"
    summarizing = "summarizing"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


# ============ 输入类型枚举 ============
class InputType(str, Enum):
    url = "url"
    file = "file"
    text = "text"
    batch = "batch"


# ============ 任务相关模型 ============
class JobCreate(BaseModel):
    """提交处理任务请求体"""
    input_type: InputType = Field(..., description="输入类型")
    input_url: Optional[str] = Field(None, description="视频URL")
    input_text: Optional[str] = Field(None, description="纯文本内容")
    input_file_path: Optional[str] = Field(None, description="上传文件路径")
    batch_urls: Optional[List[str]] = Field(None, description="批量URL列表")
    model: Optional[str] = Field(None, description="AI模型key（缺省时用默认模型）")
    summary_type: Optional[str] = Field("summary", description="总结类型（旧）")
    dimensions: Optional[Dict[str, str]] = Field(None, description="模块化提示词7维选择（新，优先于summary_type）")
    options: Optional[Dict[str, Any]] = Field({}, description="高级选项")
    do_summary: bool = Field(True, description="是否AI总结")
    do_translate: bool = Field(False, description="是否翻译")
    do_subtitle: bool = Field(False, description="是否字幕翻译")
    preview_before_summary: bool = Field(True, description="转录后先预览")
    force_new: bool = Field(False, description="强制创建新任务，跳过URL去重")


class JobResponse(BaseModel):
    """任务详情响应"""
    id: str
    title: Optional[str] = None
    platform: Optional[str] = None
    status: JobStatus = JobStatus.pending
    model: Optional[str] = None
    summary_type: Optional[str] = None
    dimensions: Optional[Dict[str, str]] = None
    options: Optional[Dict[str, Any]] = {}
    result_text: Optional[str] = None
    files: Optional[Dict[str, str]] = {}
    tags: Optional[List[str]] = []
    token_usage: Optional[Dict[str, int]] = {}
    progress_msg: Optional[str] = None
    preview_text: Optional[str] = None
    error: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class JobListResponse(BaseModel):
    """任务列表响应"""
    jobs: List[JobResponse]
    total: int


class JobSubmitResponse(BaseModel):
    """任务提交响应"""
    job_id: str
    status: JobStatus = JobStatus.pending
    message: str = "任务已提交"
    existing_job: bool = False  # 是否为已有任务（URL去重命中）
    existing_status: Optional[str] = None  # 已有任务状态
    existing_title: Optional[str] = None  # 已有任务标题


# ============ AI模型配置模型 ============
class ModelConfigCreate(BaseModel):
    """添加/编辑模型请求体"""
    key: str = Field(..., description="模型唯一标识")
    name: str = Field(..., description="显示名称")
    api_key: Optional[str] = Field(None, description="API密钥")
    endpoint: str = Field(..., description="API端点URL")
    model_id: Optional[str] = Field(None, description="API调用时的模型名")
    auth_type: str = Field("bearer", description="认证类型: bearer/direct/none")
    temperature: float = Field(0.3, description="温度参数")
    max_tokens: Optional[int] = Field(None, description="最大token数（留空=自动）")


class ModelConfigResponse(BaseModel):
    """模型配置响应"""
    key: str
    name: str
    endpoint: str
    model_id: Optional[str] = None
    auth_type: str = "bearer"
    temperature: float = 0.3
    max_tokens: int = 4096
    is_builtin: bool = False
    # api_key 不返回给前端（安全）


class ModelTestRequest(BaseModel):
    """模型测试请求"""
    key: str = Field(..., description="要测试的模型key")
    test_prompt: Optional[str] = Field("你好，请简短回复。", description="测试提示词")


class ModelTestResponse(BaseModel):
    """模型测试响应"""
    success: bool
    response: Optional[str] = None
    error: Optional[str] = None
    latency_ms: Optional[int] = None


# ============ 总结相关模型 ============
class SummaryTypeResponse(BaseModel):
    """总结类型响应"""
    key: str
    name: str
    desc: str
    icon: Optional[str] = None
    category: str = "general"


class SummaryOptionsResponse(BaseModel):
    """总结选项响应"""
    options: Dict[str, Any]


class ScenarioResponse(BaseModel):
    """场景预设响应"""
    key: str
    name: str
    summary_type: str
    tone: str
    format: str
    purpose: str


# ============ 导出相关模型 ============
class ExportRequest(BaseModel):
    """导出请求"""
    job_id: str = Field(..., description="任务ID")
    format: str = Field(..., description="导出格式: docx/pdf/html/txt")
    include_title: bool = Field(True, description="是否包含标题")


class ExportResponse(BaseModel):
    """导出响应"""
    file_path: str
    file_name: str
    format: str


# ============ 翻译相关模型 ============
class TranslateLangResponse(BaseModel):
    """翻译语言响应"""
    langs: Dict[str, Dict[str, str]]


# ============ Socket.IO 事件数据模型 ============
class ContinueSummaryData(BaseModel):
    """用户确认继续总结"""
    job_id: str
    edited_text: Optional[str] = None


class CancelPreviewData(BaseModel):
    """用户取消预览"""
    job_id: str


class CancelJobData(BaseModel):
    """用户取消任务"""
    job_id: str


class EditTranscriptData(BaseModel):
    """编辑转录文本"""
    job_id: str
    text: str


# ============ 通用响应 ============
class SuccessResponse(BaseModel):
    success: bool = True
    message: str = "操作成功"
    data: Optional[Dict[str, Any]] = None


class ErrorResponse(BaseModel):
    success: bool = False
    error: str
