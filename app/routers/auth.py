from fastapi import APIRouter

router = APIRouter()


@router.post("/kakao/callback", summary="카카오 로그인 콜백")
async def kakao_callback():
    return {"message": "자동 배포 테스트"}
