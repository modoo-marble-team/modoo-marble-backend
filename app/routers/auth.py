from fastapi import APIRouter

router = APIRouter()


@router.post("/kakao/callback", summary="카카오 로그인 콜백")
async def kakao_callback():
    return {"message": "구현 예정"}
